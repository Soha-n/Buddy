"""Ranks catalog models against detected hardware.

Two stages: a hard filter that removes models which genuinely cannot run, then a
weighted score over what remains. Reasoning strings are generated from the
computed numbers so they always describe *this* machine rather than a canned
blurb per model.
"""

from __future__ import annotations

from app.models.catalog import load_catalog
from app.models.schemas import (
    CatalogModel,
    ExcludedModel,
    FitLevel,
    ModelTier,
    Recommendation,
    SystemSpecs,
)

# Weights sum to 1.0. RAM dominates because exhausting it is the actual failure
# mode: the model either loads or it doesn't.
_W_RAM = 0.40
_W_VRAM = 0.30
_W_CPU = 0.15
_W_QUALITY = 0.15

# The OS, the browser and this app all need room, so not all RAM is available to
# the model.
_RAM_USABLE_FRACTION = 0.75

# Keep some slack on disk rather than filling the volume completely.
_DISK_USABLE_FRACTION = 0.90

# Credit given for CPU-only inference when VRAM cannot cover the model. It is a
# real option, just a slower one, so it must not score zero.
_CPU_ONLY_VRAM_CREDIT = 0.35

# A small nudge so a sensible default wins an otherwise even matchup.
_DEFAULT_BONUS = 1.5


def _usable_ram_gb(specs: SystemSpecs) -> float:
    return specs.ram.total_gb * _RAM_USABLE_FRACTION


def _score_ram(model: CatalogModel, usable_ram: float) -> float:
    """1.0 when there is comfortable headroom, falling off steeply below."""
    if model.recommended_ram_gb <= 0:
        return 1.0
    ratio = usable_ram / model.recommended_ram_gb
    if ratio >= 1.0:
        return 1.0
    # Squared below the recommendation so a tight fit is punished harder than a
    # linear falloff would.
    return max(0.0, ratio**2)


def _score_vram(model: CatalogModel, vram_gb: float) -> tuple[float, bool]:
    """Score GPU fit and report whether the model can offload to the GPU."""
    if vram_gb <= 0:
        return _CPU_ONLY_VRAM_CREDIT, False
    if vram_gb >= model.min_vram_gb:
        return 1.0, True
    # Partial offload still helps, so scale between the CPU-only floor and 1.0.
    fraction = vram_gb / model.min_vram_gb
    return _CPU_ONLY_VRAM_CREDIT + (1.0 - _CPU_ONLY_VRAM_CREDIT) * fraction, False


def _score_cpu(model: CatalogModel, cores: int, gpu_offload: bool) -> float:
    """Cores matter most when the GPU cannot take the load."""
    # Bigger models want more threads; roughly one core per 1B params, floored.
    target_cores = max(4.0, model.params_b)
    ratio = min(1.0, cores / target_cores)
    if gpu_offload:
        # GPU is carrying the work, so a weak CPU matters much less.
        return 0.7 + 0.3 * ratio
    return ratio


def _fit_level(ram_score: float, gpu_offload: bool) -> FitLevel:
    if ram_score >= 0.95 and gpu_offload:
        return "excellent"
    if ram_score >= 0.95:
        return "good"
    if ram_score >= 0.6:
        return "good"
    return "tight"


def _expected_speed(model: CatalogModel, gpu_offload: bool, ram_score: float) -> str:
    if ram_score < 0.6:
        return "Slow - likely to swap to disk"
    if gpu_offload:
        if model.params_b <= 4:
            return "Very fast (GPU accelerated)"
        return "Fast (GPU accelerated)"
    if model.params_b <= 2:
        return "Fast on CPU"
    if model.params_b <= 4:
        return "Moderate on CPU"
    if model.params_b <= 9:
        return "Slow on CPU"
    return "Very slow on CPU"


def _build_reasoning(
    model: CatalogModel,
    specs: SystemSpecs,
    usable_ram: float,
    vram_gb: float,
    gpu_offload: bool,
    ram_score: float,
) -> list[str]:
    """Explain the fit using this machine's actual numbers."""
    reasons: list[str] = []

    if ram_score >= 1.0:
        reasons.append(
            f"Needs about {model.recommended_ram_gb} GB of RAM; you have "
            f"{specs.ram.total_gb} GB total (~{usable_ram:.1f} GB usable) - "
            "comfortable fit."
        )
    elif ram_score >= 0.6:
        reasons.append(
            f"Wants {model.recommended_ram_gb} GB of RAM but runs from "
            f"{model.min_ram_gb} GB; your ~{usable_ram:.1f} GB usable is workable "
            "with less headroom for long conversations."
        )
    else:
        reasons.append(
            f"Tight on memory: wants {model.recommended_ram_gb} GB, you have about "
            f"{usable_ram:.1f} GB usable. Expect slowdowns."
        )

    if vram_gb <= 0:
        reasons.append("No usable GPU detected, so this runs on CPU.")
    elif gpu_offload:
        reasons.append(
            f"{vram_gb:.1f} GB VRAM covers the {model.min_vram_gb} GB this model "
            "wants, so it can run on the GPU."
        )
    else:
        reasons.append(
            f"{vram_gb:.1f} GB VRAM is under the {model.min_vram_gb} GB needed for "
            "full GPU offload; part of the work falls back to CPU."
        )

    cores = specs.cpu.physical_cores or specs.cpu.logical_cores
    if cores and not gpu_offload:
        reasons.append(f"{cores} physical cores available for CPU inference.")

    reasons.append(
        f"{model.download_size_gb} GB download; {specs.disk.free_gb} GB free on disk."
    )
    reasons.append("Strengths: " + ", ".join(model.strengths) + ".")

    return reasons


def _hard_filter(
    model: CatalogModel, specs: SystemSpecs
) -> str | None:
    """Return an exclusion reason, or None if the model is viable."""
    if specs.ram.total_gb and model.min_ram_gb > specs.ram.total_gb:
        return (
            f"Needs at least {model.min_ram_gb} GB RAM; system has "
            f"{specs.ram.total_gb} GB."
        )
    usable_disk = specs.disk.free_gb * _DISK_USABLE_FRACTION
    if specs.disk.free_gb and model.download_size_gb > usable_disk:
        return (
            f"Download is {model.download_size_gb} GB but only "
            f"{specs.disk.free_gb} GB is free."
        )
    # Deliberately no GPU filter: CPU-only inference is valid, just slower.
    return None


def _score_all(specs: SystemSpecs) -> tuple[list[Recommendation], list[ExcludedModel]]:
    """Score every viable catalog model against these specs, sorted best-first.

    Shared by rank_models (top-3, diversified) and rank_all (every tier).
    """
    usable_ram = _usable_ram_gb(specs)
    vram_gb = specs.best_vram_gb
    cores = specs.cpu.physical_cores or specs.cpu.logical_cores or 1

    scored: list[Recommendation] = []
    excluded: list[ExcludedModel] = []

    for model in load_catalog():
        reason = _hard_filter(model, specs)
        if reason is not None:
            excluded.append(ExcludedModel(name=model.name, reason=reason))
            continue

        ram_score = _score_ram(model, usable_ram)
        vram_score, gpu_offload = _score_vram(model, vram_gb)
        cpu_score = _score_cpu(model, cores, gpu_offload)
        quality_score = (model.quality_tier - 1) / 4  # tier 1..5 -> 0..1

        total = (
            _W_RAM * ram_score
            + _W_VRAM * vram_score
            + _W_CPU * cpu_score
            + _W_QUALITY * quality_score
        ) * 100
        if model.is_default:
            total += _DEFAULT_BONUS

        scored.append(
            Recommendation(
                model=model,
                score=round(min(100.0, total), 1),
                fit=_fit_level(ram_score, gpu_offload),
                reasoning=_build_reasoning(
                    model, specs, usable_ram, vram_gb, gpu_offload, ram_score
                ),
                expected_speed=_expected_speed(model, gpu_offload, ram_score),
                gpu_offload=gpu_offload,
            )
        )

    scored.sort(key=lambda r: r.score, reverse=True)
    return scored, excluded


def rank_models(
    specs: SystemSpecs, limit: int = 3
) -> tuple[list[Recommendation], list[ExcludedModel]]:
    """Score the catalog and return the top `limit` picks plus exclusions."""
    scored, excluded = _score_all(specs)
    return _diversify(scored, limit), excluded


# Tiers are ordered by how well a model fits AND how fast it will actually
# run on this machine - not by raw model size or quality_tier. A small model
# that flies beats a big one that strains, even if the big one is "smarter" on
# paper. Quality only breaks ties between models that are equally fast.
_BEST_SPEED_MIN = 0.75
_BETTER_SPEED_MIN = 0.45


def _speed_score(rec: Recommendation, ram_score: float) -> float:
    """0..1: how fast this model will actually feel on this machine.

    GPU offload dominates (inference is CPU-bound otherwise), RAM headroom
    matters because swapping tanks speed regardless of anything else, and
    smaller models are inherently faster once those two are equal.
    """
    gpu_component = 1.0 if rec.gpu_offload else 0.35
    size_component = 1.0 / (1.0 + rec.model.params_b / 8.0)
    return round(0.5 * gpu_component + 0.3 * ram_score + 0.2 * size_component, 3)


def tier_for(rec: Recommendation, speed_score: float) -> ModelTier:
    """Bucket a scored recommendation into Best / Better / Good by speed+fit.

    A tight fit is an automatic ceiling at "Good" regardless of speed: a model
    that struggles to run has no business being called Best or Better.
    """
    if rec.fit == "tight":
        return "good"
    if speed_score >= _BEST_SPEED_MIN and rec.fit == "excellent":
        return "best"
    if speed_score >= _BETTER_SPEED_MIN:
        return "better"
    return "good"


def rank_all(
    specs: SystemSpecs,
) -> tuple[dict[str, list[Recommendation]], list[ExcludedModel]]:
    """Score and bucket every viable catalog model into best/better/good tiers."""
    scored, excluded = _score_all(specs)
    usable_ram = _usable_ram_gb(specs)

    tiers: dict[str, list[Recommendation]] = {"best": [], "better": [], "good": []}
    for rec in scored:
        ram_score = _score_ram(rec.model, usable_ram)
        speed = _speed_score(rec, ram_score)
        tiers[tier_for(rec, speed)].append(rec)

    # Within each tier, rank by overall score - the highest-scoring model for
    # this machine leads, in every tab.
    for tier_recs in tiers.values():
        tier_recs.sort(key=lambda r: r.score, reverse=True)

    return tiers, excluded


def _diversify(scored: list[Recommendation], limit: int) -> list[Recommendation]:
    """Prefer distinct families so the top 3 are not near-identical variants.

    Without this, one strong family can sweep every slot (three qwen3 sizes),
    which is technically correct but useless as a choice.
    """
    picked: list[Recommendation] = []
    seen_families: set[str] = set()

    for rec in scored:
        if len(picked) >= limit:
            break
        if rec.model.family in seen_families:
            continue
        picked.append(rec)
        seen_families.add(rec.model.family)

    # Backfill from the remainder if there were not enough distinct families.
    if len(picked) < limit:
        for rec in scored:
            if len(picked) >= limit:
                break
            if rec not in picked:
                picked.append(rec)
        picked.sort(key=lambda r: r.score, reverse=True)

    return picked
