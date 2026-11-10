"""Entrypoint for hosts that look for a top-level `app` (for example Vercel)."""

from sentinel_api.entrypoint import app

__all__ = ["app"]
