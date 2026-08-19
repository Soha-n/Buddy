"""Loads and validates the curated model catalog."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.models.schemas import CatalogModel

_CATALOG_PATH = Path(__file__).with_name("catalog.json")


@lru_cache(maxsize=1)
def load_catalog() -> list[CatalogModel]:
    """Parse catalog.json into validated models. Cached for process lifetime."""
    raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    return [CatalogModel(**entry) for entry in raw["models"]]


def find_model(name: str) -> CatalogModel | None:
    """Look up a catalog entry by its exact Ollama tag."""
    for model in load_catalog():
        if model.name == name:
            return model
    return None
