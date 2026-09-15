"""Real-CPU C8 prototype substrate.

The package initializer intentionally imports no role implementation. In particular,
worker and fault-harness processes must not acquire ContinuityCore transitively.
"""

__all__: tuple[str, ...] = ()
