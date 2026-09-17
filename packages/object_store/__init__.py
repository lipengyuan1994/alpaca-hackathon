"""Content-addressed immutable artifact storage adapter."""

from .local import LocalObjectStore
from .s3 import S3ObjectStore

__all__ = ["LocalObjectStore", "S3ObjectStore"]
