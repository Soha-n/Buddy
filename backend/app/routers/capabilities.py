"""Model capability lookup, so the UI can gate image uploads.

The composer needs to know whether the selected model can see images *before*
the user sends anything, which is what turns a confusing silent failure ("I sent
a chart and it answered about nothing") into an explicit choice of model.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import ModelCapabilities, VisionCheckResponse
from app.services import capabilities as caps

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


@router.get("/vision-check", response_model=VisionCheckResponse)
async def vision_check(model: str) -> VisionCheckResponse:
    """Whether this model accepts images, plus what to switch to if not.

    Alternatives are always included, even when the answer is yes: the response
    is cached client-side per model, and returning them here means the warning
    UI never needs a second round trip at the moment it has to render.
    """
    if not model.strip():
        raise HTTPException(status_code=400, detail="model is required")

    supports = await caps.supports_vision(model)
    installed = await caps.list_vision_models()

    return VisionCheckResponse(
        model=model,
        supports_vision=supports,
        installed_vision_models=installed,
    )


@router.get("/{model:path}", response_model=ModelCapabilities)
async def model_capabilities(model: str) -> ModelCapabilities:
    """Raw capability list for a tag, as reported by Ollama."""
    capability_list = await caps.get_capabilities(model)
    return ModelCapabilities(
        model=model,
        capabilities=capability_list,
        supports_vision=caps.VISION_CAPABILITY in capability_list,
    )
