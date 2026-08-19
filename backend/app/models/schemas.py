"""Pydantic request/response models. Mirrored in frontend/src/types/api.ts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FitLevel = Literal["excellent", "good", "tight"]
ModelTier = Literal["best", "better", "good"]


# --------------------------------------------------------------------------- #
# System specs
# --------------------------------------------------------------------------- #


class CpuInfo(BaseModel):
    name: str
    physical_cores: int | None = None
    logical_cores: int | None = None
    max_clock_mhz: int | None = None
    architecture: str


class GpuInfo(BaseModel):
    name: str
    vram_gb: float | None = None
    vram_free_gb: float | None = None
    driver_version: str | None = None
    vendor: str
    source: str = Field(
        description="Where VRAM came from: nvidia-smi, registry, cim, or unknown."
    )
    vram_reliable: bool = Field(
        default=True,
        description="False when the value came from Win32_VideoController.AdapterRAM, "
        "which is a 32-bit field and saturates at 4 GB.",
    )


class RamInfo(BaseModel):
    total_gb: float
    available_gb: float | None = None


class DiskInfo(BaseModel):
    path: str
    total_gb: float
    free_gb: float


class OsInfo(BaseModel):
    system: str
    release: str
    version: str
    machine: str


class SystemSpecs(BaseModel):
    cpu: CpuInfo
    ram: RamInfo
    gpus: list[GpuInfo]
    disk: DiskInfo
    os: OsInfo
    warnings: list[str] = []
    detected_at: str

    @property
    def best_vram_gb(self) -> float:
        """VRAM of the most capable GPU, ignoring values we know to be bogus."""
        usable = [
            g.vram_gb
            for g in self.gpus
            if g.vram_gb is not None and g.vram_reliable
        ]
        return max(usable) if usable else 0.0


# --------------------------------------------------------------------------- #
# Catalog + recommendations
# --------------------------------------------------------------------------- #


class CatalogModel(BaseModel):
    id: str
    name: str = Field(description="Exact Ollama tag, e.g. llama3.2:3b")
    family: str
    params_b: float
    download_size_gb: float
    min_ram_gb: float
    recommended_ram_gb: float
    min_vram_gb: float
    quality_tier: int = Field(ge=1, le=5)
    strengths: list[str]
    description: str
    is_default: bool = False
    tags: list[str] = []


class Recommendation(BaseModel):
    model: CatalogModel
    score: float
    fit: FitLevel
    reasoning: list[str]
    expected_speed: str
    gpu_offload: bool


class ExcludedModel(BaseModel):
    name: str
    reason: str


class RecommendationsResponse(BaseModel):
    specs: SystemSpecs
    recommendations: list[Recommendation]
    excluded: list[ExcludedModel]
    catalog_size: int = Field(
        description="Total models considered, so the UI can say how many were ranked."
    )


class TieredRecommendation(Recommendation):
    tier: ModelTier


class TiersResponse(BaseModel):
    specs: SystemSpecs
    best: list[TieredRecommendation]
    better: list[TieredRecommendation]
    good: list[TieredRecommendation]
    excluded: list[ExcludedModel]
    catalog_size: int


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


class OllamaStatus(BaseModel):
    installed: bool
    running: bool
    version: str | None = None
    binary_path: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    ollama: OllamaStatus


# --------------------------------------------------------------------------- #
# Models / chat
# --------------------------------------------------------------------------- #


class InstalledModel(BaseModel):
    name: str
    size_bytes: int
    modified_at: str | None = None


class InstalledModelsResponse(BaseModel):
    models: list[InstalledModel]


class PullRequest(BaseModel):
    model: str


class PreloadRequest(BaseModel):
    model: str


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
    images: list[str] | None = Field(
        default=None,
        description="base64-encoded images for this turn, as Ollama's /api/chat expects. "
        "Only ever populated for a vision-capable model.",
    )


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    options: dict[str, float | int | str] | None = None
    conversation_id: str | None = Field(
        default=None,
        description="Existing conversation to append to. Omit to create a new one.",
    )
    attachment_ids: list[str] = Field(
        default_factory=list,
        description="Attachments sent with this turn. Images among them are passed "
        "to the model; documents are already indexed and reached via retrieval.",
    )
    web_search: bool = Field(
        default=False,
        description="Whether to search the web for this turn. Off by default, and "
        "per-message rather than a stored setting: the user decides each time, "
        "because local models judge their own knowledge cutoff unreliably.",
    )


# --------------------------------------------------------------------------- #
# Model search (catalog + live library)
# --------------------------------------------------------------------------- #


class LibraryEntry(BaseModel):
    """A model found via live search, not the curated catalog.

    Unscored deliberately: scoring needs min_ram_gb/min_vram_gb/quality_tier,
    which a library page scrape cannot reliably produce.
    """

    name: str
    description: str | None = None
    size_hint: str | None = Field(
        default=None, description="Raw size text as shown on ollama.com, e.g. '4.7GB'."
    )


class LibrarySearchResponse(BaseModel):
    query: str
    entries: list[LibraryEntry]
    source: Literal["live", "stale_cache", "catalog_only"]
    stale: bool = False


# --------------------------------------------------------------------------- #
# Conversations
# --------------------------------------------------------------------------- #


class MessageRecord(BaseModel):
    id: int
    role: Literal["system", "user", "assistant"]
    content: str
    created_at: str
    model_used_for_this_turn: str | None = None
    attachments: list["AttachmentRecord"] = Field(
        default_factory=list,
        description="Files sent with this turn, for re-rendering the transcript.",
    )


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    last_model: str | None = None


class ConversationDetail(ConversationSummary):
    messages: list[MessageRecord]


class ConversationsListResponse(BaseModel):
    conversations: list[ConversationSummary]


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


# --------------------------------------------------------------------------- #
# Attachments (RAG)
# --------------------------------------------------------------------------- #

AttachmentKind = Literal["pdf", "docx", "table", "text", "image", "unsupported"]
AttachmentStatus = Literal["pending", "ready", "error"]


class AttachmentRecord(BaseModel):
    id: str
    conversation_id: str
    filename: str
    kind: AttachmentKind
    mime_type: str | None = None
    size_bytes: int
    created_at: str
    status: AttachmentStatus
    error: str | None = None
    chunk_count: int = 0
    has_description: bool = Field(
        default=False,
        description="Images only: whether a vision description has been generated, "
        "which is what lets a text-only model reason about the image later.",
    )


class AttachmentsListResponse(BaseModel):
    attachments: list[AttachmentRecord]


class RetrievedChunk(BaseModel):
    filename: str
    locator: str | None
    content: str
    score: float


class UploadResponse(BaseModel):
    attachments: list[AttachmentRecord]
    rejected: list[ExcludedModel] = Field(
        default_factory=list,
        description="Files not accepted, reusing {name, reason}.",
    )


# --------------------------------------------------------------------------- #
# Model capabilities (vision gating)
# --------------------------------------------------------------------------- #


class ModelCapabilities(BaseModel):
    model: str
    capabilities: list[str]
    supports_vision: bool


class VisionCheckResponse(BaseModel):
    model: str
    supports_vision: bool
    installed_vision_models: list[str] = Field(
        default_factory=list,
        description="Vision-capable models already on disk, offered as a switch target.",
    )


# --------------------------------------------------------------------------- #
# Chart code execution
# --------------------------------------------------------------------------- #


class RunCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=20_000)
    conversation_id: str | None = Field(
        default=None,
        description="Scopes which uploaded data files the code may read.",
    )


class RunCodeResponse(BaseModel):
    ok: bool
    stdout: str = ""
    error: str | None = None
    image_base64: str | None = None
    duration_s: float = 0.0


# --------------------------------------------------------------------------- #
# Web search
# --------------------------------------------------------------------------- #


class WebSearchStatus(BaseModel):
    """Whether search can work at all, so the UI can explain a dead toggle."""

    available: bool
    provider: str = Field(
        description="Provider that would be used: searxng, duckduckgo, or none."
    )
    searxng_detected: bool = False
    detail: str | None = None


class SearchCitation(BaseModel):
    """One source behind an answer, streamed to the UI before the tokens."""

    index: int
    title: str
    url: str
    #: True when the full page was read, not just the search snippet.
    fetched: bool = False


# --------------------------------------------------------------------------- #
# User context (location + time)
# --------------------------------------------------------------------------- #


class LocationResponse(BaseModel):
    city: str | None = None
    region: str | None = None
    country: str | None = None
    timezone: str | None = None
    label: str
    #: "manual" (typed by the user), "os_gps" (Windows Location Service),
    #: "os_region" (regional settings - country only), or "unavailable".
    source: Literal["manual", "os_gps", "os_region", "unavailable"]
    #: True when the OS can give exact coordinates if the user permits it, so the
    #: UI can offer precise detection only where it would work.
    precise_available: bool = True
    #: Local date/time as this machine sees it, so the UI can show what the
    #: model was told without computing it a second way.
    local_date: str
    local_time: str


class SetLocationRequest(BaseModel):
    city: str = Field(min_length=1, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
