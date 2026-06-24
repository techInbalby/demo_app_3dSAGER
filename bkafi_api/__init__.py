"""Flask blueprint for the legacy BKAFI loading + per-building lookup routes."""

from .routes import bkafi_api_bp

__all__ = ['bkafi_api_bp']
