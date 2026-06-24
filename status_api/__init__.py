"""Flask blueprint for per-building status + classifier metrics summary."""

from .routes import status_api_bp

__all__ = ['status_api_bp']
