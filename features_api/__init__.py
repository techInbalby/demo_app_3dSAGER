"""Flask blueprint for geometric-feature calculation + per-building lookups."""

from .routes import features_api_bp

__all__ = ['features_api_bp']
