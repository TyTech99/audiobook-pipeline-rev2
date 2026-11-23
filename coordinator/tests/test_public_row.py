"""Regression: the TTS worker swaps the queue message for the API row
before launching the recipe container (worker.py idempotency check), so
recipe selection must ride on the row itself.

Found live on job 3efd88ec (2026-11-22): metadata.voice_recipe.image
override reached the queue message but NOT the API row, so the worker
silently launched the default CosyVoice recipe.
"""

from __future__ import annotations

from datetime import datetime, timezone

from coordinator.services.jobs import _public


def _row(convert=None, metadata=None):
    return {
        "job_id": "j1",
        "type": "convert",
        "status": "submitted",
        "input_format": "epub",
        "input_url": "file:///input/book.epub",
        "package_url": None,
        "output_url": None,
        "metadata": metadata,
        "prep": {},
        "convert": convert,
        "progress": {},
        "error": None,
        "retry_count": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


def test_public_row_surfaces_metadata_recipe_selection():
    row = _public(
        _row(
            metadata={
                "voice_recipe": {"image": "audiobook-recipe-ggardner-indextts:local"},
                "performance_style": "noir-mystery",
            }
        )
    )
    assert row["voice_recipe"] == {"image": "audiobook-recipe-ggardner-indextts:local"}
    assert row["performance_style"] == "noir-mystery"


def test_public_row_convert_phase_overrides_metadata():
    """Same precedence as the retry builder: convert-phase values win."""
    row = _public(
        _row(
            convert={"voice_recipe": {"voice": "michael"}, "performance_style": "warm"},
            metadata={"voice_recipe": {"image": "x"}, "performance_style": "noir-mystery"},
        )
    )
    assert row["voice_recipe"] == {"voice": "michael"}
    assert row["performance_style"] == "warm"


def test_public_row_defaults_when_nothing_declared():
    row = _public(_row())
    assert row["voice_recipe"] == {}
    assert row["performance_style"] == "neutral"
