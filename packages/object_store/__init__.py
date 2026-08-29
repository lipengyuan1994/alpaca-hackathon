"""Content-addressed immutable artifact storage adapter."""

from .local import LocalObjectStore

__all__ = ["LocalObjectStore"]
