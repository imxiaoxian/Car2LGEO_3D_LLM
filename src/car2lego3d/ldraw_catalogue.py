"""Catalogue of real LDraw parts, used to constrain LLM output to buildable parts.

The LLM must choose from parts that actually exist in the installed LDraw
library; inventing a part number produces a model that cannot be built or
rendered. This module indexes the library once and answers existence queries
cheaply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re


# Directories that hold real part geometry rather than sub-primitives.
PART_DIRECTORIES = ("parts", "p")
SKIP_PREFIXES = ("s/", "48/")


@dataclass
class PartCatalogue:
    """Index of part IDs available in one or more LDraw roots."""

    roots: tuple[Path, ...]
    part_ids: frozenset[str] = field(default_factory=frozenset)
    _by_base: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_roots(cls, roots) -> "PartCatalogue":
        resolved = tuple(Path(r).resolve() for r in roots if r and Path(r).is_dir())
        ids: set[str] = set()
        for root in resolved:
            for folder in PART_DIRECTORIES:
                directory = root / folder
                if not directory.is_dir():
                    continue
                for entry in directory.iterdir():
                    if entry.is_file() and entry.suffix.lower() == ".dat":
                        ids.add(entry.stem.lower())
        catalogue = cls(roots=resolved, part_ids=frozenset(ids))
        catalogue._index_bases()
        return catalogue

    @classmethod
    def from_environment(cls, override=None) -> "PartCatalogue":
        """Build the catalogue from the configured LDraw roots."""
        roots = []
        if override:
            roots.append(override)
        env = os.environ.get("CAR2LEGO_LDRAW_ROOTS") or os.environ.get("CAR2LEGO_LDRAW_ROOT")
        if env:
            roots.extend(p for p in re.split(r"[;:]", env) if p)
        # Studio ships the library this project already uses for its exports.
        roots.append("D:/lego/Studio 2.0/ldraw")
        return cls.from_roots(roots)

    def _index_bases(self) -> None:
        for part_id in self.part_ids:
            base = re.sub(r"[a-z]+$", "", part_id) or part_id
            self._by_base.setdefault(base, []).append(part_id)

    def __len__(self) -> int:
        return len(self.part_ids)

    def has(self, part_id: str) -> bool:
        return str(part_id).strip().lower() in self.part_ids

    def resolve(self, part_id: str) -> str | None:
        """Return the canonical part ID, or the closest lettered variant.

        LDraw numbers lettered variants of a shape (``3040`` -> ``3040a``), so a
        model asking for the base number should still build when only variants
        are installed.
        """
        candidate = str(part_id).strip().lower()
        if not candidate:
            return None
        if candidate in self.part_ids:
            return candidate
        variants = sorted(self._by_base.get(candidate, ()))
        return variants[0] if variants else None

    def sample(self, prefix: str, limit: int = 40) -> list[str]:
        """Return up to ``limit`` part IDs sharing a leading number."""
        return sorted(p for p in self.part_ids if p.startswith(prefix))[:limit]


def load_catalogue(override=None) -> PartCatalogue:
    """Load the catalogue, caching it for the process lifetime."""
    global _CACHED
    try:
        return _CACHED
    except NameError:
        pass
    _CACHED = PartCatalogue.from_environment(override)
    return _CACHED
