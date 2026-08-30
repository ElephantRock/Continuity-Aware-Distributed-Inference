"""Deterministic discrete-event simulation substrate for CADI C2."""

from .engine import DiscreteEventSimulator
from .events import EventKind, SimEvent

__all__ = ["DiscreteEventSimulator", "EventKind", "SimEvent"]
