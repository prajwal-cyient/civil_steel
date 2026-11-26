"""IDEA connection design errors shared by the adapter and request builder."""


class IdeaStaticaJointError(RuntimeError):
    """Raised when a joint cannot be resolved, evaluated, or updated."""
