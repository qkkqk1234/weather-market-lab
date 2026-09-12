"""wxlab -- a research harness for daily-high-temperature prediction markets.

Read-only by construction: this package fetches public data, fits a baseline
distribution, and scores it against recorded market prices. There is no order
placement path anywhere in it.
"""

from .data import Bucket, Event, Metar, Obs, load_events, load_metar, load_quotes
from .model import DeltaModel

__version__ = "0.1.0"

__all__ = [
    "Bucket", "Event", "Metar", "Obs",
    "load_events", "load_metar", "load_quotes",
    "DeltaModel", "__version__",
]
