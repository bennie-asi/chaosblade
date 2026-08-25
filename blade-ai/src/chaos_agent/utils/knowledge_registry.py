"""Knowledge document metadata registry — auto-discovered from YAML frontmatter.

Each knowledge/*.md file should contain YAML frontmatter with:
  title, topics, fault_types, summary

The registry scans the knowledge/ directory once at first call and caches the result.
To force re-scan after adding/removing files, call rebuild_registry().
"""

import warnings
from pathlib import Path
from typing import Optional

from chaos_agent.skills.loader import parse_frontmatter

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

# Canonical phase names accepted by the knowledge index filter
# (get_knowledge_summary_section). A typo'd phase value would otherwise
# silently exclude the doc from every phase index.
_VALID_PHASES = frozenset({"plan", "execute", "verify", "recover"})


def _build_registry() -> list[dict]:
    """Scan knowledge/ directory, parse frontmatter from each .md file.

    Returns a sorted list of metadata dicts. Files without valid frontmatter
    are skipped with a warning — they will NOT appear in the knowledge index
    and cannot be read via read_knowledge_resource (which validates against
    this registry).
    """
    registry = []
    if not KNOWLEDGE_DIR.is_dir():
        return registry

    for md_file in sorted(KNOWLEDGE_DIR.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception as exc:
            warnings.warn(
                f"Failed to read knowledge file {md_file.name}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            continue

        frontmatter = parse_frontmatter(content)

        if frontmatter is None:
            warnings.warn(
                f"Knowledge file '{md_file.name}' has no YAML frontmatter — "
                f"skipped from knowledge index. Add frontmatter with title/topics/fault_types/summary.",
                RuntimeWarning,
                stacklevel=2,
            )
            continue

        # Validate required fields
        missing = [f for f in ("title", "topics", "fault_types", "summary") if f not in frontmatter]
        if missing:
            warnings.warn(
                f"Knowledge file '{md_file.name}' frontmatter missing required fields: {missing} — skipped.",
                RuntimeWarning,
                stacklevel=2,
            )
            continue

        # Normalize fault_types: ["all"] means all fault types
        fault_types = frontmatter["fault_types"]
        if fault_types == ["all"]:
            fault_types = ["all fault types"]

        # Optional phase relevance: which prompt phases (plan / execute /
        # verify / recover) the doc serves. Absent → relevant everywhere.
        phases = frontmatter.get("phases")
        if phases is not None:
            if not isinstance(phases, list):
                phases = [phases]
            invalid = sorted(set(phases) - _VALID_PHASES)
            if invalid:
                warnings.warn(
                    f"Knowledge file '{md_file.name}' frontmatter has invalid "
                    f"phases {invalid} (valid: {sorted(_VALID_PHASES)}) — "
                    f"doc would be hidden from those phase indexes.",
                    RuntimeWarning,
                    stacklevel=2,
                )

        registry.append({
            "filename": md_file.name,
            "title": frontmatter["title"],
            "topics": frontmatter["topics"],
            "fault_types": fault_types,
            "summary": frontmatter["summary"],
            "phases": phases,
            "size_chars": len(content),
        })

    return registry


# Module-level registry — built once at import, can be rebuilt on demand
_registry_cache: Optional[list[dict]] = None


def get_knowledge_registry() -> list[dict]:
    """Get the knowledge registry (cached after first call)."""
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = _build_registry()
    return _registry_cache


def rebuild_registry() -> list[dict]:
    """Force re-scan of knowledge/ directory. Call after adding/removing files."""
    global _registry_cache
    _registry_cache = _build_registry()
    return _registry_cache
