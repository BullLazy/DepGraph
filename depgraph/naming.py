"""Ecosystem-agnostic and Python-specific name normalization helpers."""

from __future__ import annotations

import re

try:  # pragma: no cover - exercised only when packaging is absent
    from packaging.utils import canonicalize_name
except ImportError:  # pragma: no cover
    canonicalize_name = None


def normalize_name(name):
    """Normalize a Python package name (PEP 503 / PEP 685)."""
    if canonicalize_name is None:  # pragma: no cover - fallback path
        return re.sub(r"[-_.]+", "-", name).lower()
    return canonicalize_name(name)


def normalize_extra(extra):
    """Normalize an extra name (PEP 685 reuses PEP 503 normalization)."""
    return normalize_name(extra)


def normalize_simple(name):
    """A conservative, case-insensitive normalization for non-Python names.

    Collapses surrounding whitespace and lowercases, but does not otherwise
    rewrite separators, since doing so can change package identity in other
    ecosystems (for example Go module paths).
    """
    return name.strip().lower()
