"""Flask blueprint for single-building CityJSON extraction + cross-file lookup."""

from .routes import building_api_bp

__all__ = ['building_api_bp']
