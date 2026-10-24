"""Importing this package registers every table on Base.metadata."""

from app.models import clinical, knowledge, platform  # noqa: F401
