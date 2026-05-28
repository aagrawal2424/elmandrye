"""Typed errors for the source chain — drive routing decisions in run_source_chain.

Mirrors generate_image.py's error model: classify auth/transient/rate-limit so
the caller can decide whether to advance providers, retry, or alert.
"""


class SourceError(Exception):
    """Base class for any expected source failure."""


class SourceAuthError(SourceError):
    """API key missing, invalid, or revoked."""


class SourceRateLimitError(SourceError):
    """Source signaled 429 or quota exhausted."""


class SourceTransientError(SourceError):
    """Network/5xx/timeout — would likely succeed on retry."""


class NoIdeasError(Exception):
    """ONLY raised when every source — including the evergreen reserve — has
    nothing left. Caller MUST alert (Resend) and either republish from a
    backup queue or fail-loud in CI. Should never happen by design."""
