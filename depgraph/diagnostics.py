"""Error types and structured diagnostics for DepGraph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"
LEVEL_INFO = "info"


class DepGraphError(Exception):
    """Base class for all DepGraph errors."""


class ManifestError(DepGraphError, ValueError):
    """A manifest was found but could not be parsed."""


class UnsupportedProjectError(DepGraphError, ValueError):
    """No supported manifest could be analyzed."""

    def __init__(self, message, notes=()):
        self.notes = list(notes)
        super().__init__(message)


@dataclass
class Diagnostic:
    level: str
    code: str
    message: str
    source: Optional[str] = None

    def to_dict(self):
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "source": self.source,
        }
