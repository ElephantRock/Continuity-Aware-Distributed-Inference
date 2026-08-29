class ContinuityError(Exception):
    """Base error for deterministic Continuity semantics."""

class InvalidTransition(ContinuityError):
    pass

class InsufficientEvidence(ContinuityError):
    pass

class SemanticViolation(ContinuityError):
    pass
