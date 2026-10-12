"""Shared, language-neutral dependency model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Dependency categories (scopes). Not every ecosystem uses every category.
RUNTIME = "runtime"
DEV = "dev"
OPTIONAL = "optional"
PEER = "peer"
BUILD = "build"
TEST = "test"
PLATFORM = "platform"
UNKNOWN = "unknown"

ALL_CATEGORIES = (RUNTIME, DEV, OPTIONAL, PEER, BUILD, TEST, PLATFORM, UNKNOWN)


@dataclass
class Dependency:
    """A single declared (and optionally resolved) dependency edge."""

    ecosystem: str
    name: str
    normalized_name: str
    category: str = RUNTIME
    specifier: Optional[str] = None
    resolved_version: Optional[str] = None
    url: Optional[str] = None
    extras: list = field(default_factory=list)
    marker: Optional[str] = None
    direct: bool = True
    source: Optional[str] = None
    provenance: Optional[str] = None

    def identity(self):
        """Identity key used for de-duplication.

        Includes ecosystem, version constraint, marker, extras, source URL,
        category, resolved version, and directness so that meaningful
        distinctions are preserved rather than merged by display name.
        """
        return (
            self.ecosystem,
            self.normalized_name,
            self.specifier,
            self.marker,
            tuple(self.extras),
            self.url,
            self.category,
            self.resolved_version,
            self.direct,
        )

    def sort_key(self):
        return (
            self.ecosystem,
            self.normalized_name,
            self.name,
            self.category,
            self.specifier or "",
            self.marker or "",
            self.url or "",
            tuple(self.extras),
            self.resolved_version or "",
        )

    def to_dict(self):
        return {
            "ecosystem": self.ecosystem,
            "name": self.name,
            "normalized_name": self.normalized_name,
            "category": self.category,
            "specifier": self.specifier,
            "resolved_version": self.resolved_version,
            "url": self.url,
            "extras": list(self.extras),
            "marker": self.marker,
            "direct": self.direct,
            "source": self.source,
            "provenance": self.provenance,
        }
