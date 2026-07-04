"""US-panel domain — a reproducible US state x year multi-source data panel.

Fetches + aligns + caches a tidy state-year table (Census ACS population/income/
foreign-born, BLS unemployment, Census PEP net migration) as the analysis spine
for "what moves with what across US states over time". Discovered via the
``facetwork.domains`` entry point in pyproject.toml.
"""

from __future__ import annotations

from pathlib import Path

from facetwork.domains import DomainPackage

from .handlers import register_all_registry_handlers

domain = DomainPackage(
    name="uspanel",
    ffl_dir=Path(__file__).parent / "ffl",
    register_handlers=register_all_registry_handlers,
)
