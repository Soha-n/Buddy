"""Loads and validates the curated model catalog."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.models.schemas import CatalogModel
from app.paths import resource_root


def _catalog_path() -> Path:
    """Locate catalog.json in both a source tree and a frozen bundle.

    Beside this module when running from source. Frozen, the .py files live
    inside the bundle but data files are only there if the spec copied them, so
    the bundled copy is looked up through resource_root and the source-relative
    path is kept as a fallback.
    """
    bundled = resource_root() / "app" / "models" / "catalog.json"
    if bundled.exists():
        return bundled
    return Path(__file__).with_name("catalog.json")


@lru_cache(maxsize=1)
def load_catalog() -> list[CatalogModel]:
    """Parse catalog.json into validated models. Cached for process lifetime."""
    raw = json.loads(_catalog_path().read_text(encoding="utf-8"))
    return [CatalogModel(**entry) for entry in raw["models"]]


def find_model(name: str) -> CatalogModel | None:
    """Look up a catalog entry by its exact Ollama tag."""
    for model in load_catalog():
        if model.name == name:
            return model
    return None
