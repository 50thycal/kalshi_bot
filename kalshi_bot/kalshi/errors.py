"""Kalshi client exception hierarchy."""

from __future__ import annotations


class KalshiError(Exception):
    """Base class for all Kalshi client errors."""


class AuthError(KalshiError):
    """Authentication / authorization failure (401/403). Hard-fail, fail-closed."""


class TransientError(KalshiError):
    """Retryable transient error (5xx, 429, or network)."""


class KalshiAPIError(KalshiError):
    """Non-retryable 4xx API error."""

    def __init__(self, status_code: int, message: str, path: str | None = None):
        self.status_code = status_code
        self.message = message  # the response body, for diagnostics (e.g. invalid_parameters RCA)
        self.path = path
        super().__init__(f"Kalshi API error {status_code} on {path}: {message}")
