"""Flask blueprint that drives the online inference pipeline."""

from .routes import pipeline_bp

__all__ = ['pipeline_bp']
