"""Optional XMP classification bridge (no-op when Fido XMP helpers absent)."""

from __future__ import annotations


def read_classification(_path: str) -> str:
    return ""


def display_for_ui(_value: str) -> str:
    return "Unclassified"
