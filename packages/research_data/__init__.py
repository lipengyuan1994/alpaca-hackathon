"""Read-only, immutable Alpaca evidence collection for offline research."""

from .collector import ResearchDataCollector, ResearchDataError

__all__ = ("ResearchDataCollector", "ResearchDataError")
