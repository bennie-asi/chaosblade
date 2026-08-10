"""Runtime reader for the compiled capabilities registry — the SINGLE source
of truth for what blade-ai can inject.

The registry is produced by ``blade-ai capabilities-sync`` (LLM distills each
skill case's markdown into an executable capability definition) and shipped
inside the package at ``chaos_agent/_capabilities/capabilities_{locale}.json``
— installing a package version gives you exactly that version's capabilities.

Resolution order in :func:`load_capabilities`:

1. explicit ``path`` argument (tests / callers with a custom location)
2. locally generated file ``~/.blade-ai/memory/skill_capabilities.json`` —
   only when it is v2 format (developer override; validated against the
   current skills-dir fingerprint, stale → :class:`CapabilitiesStaleError`)
3. package-bundled ``capabilities_{locale}.json``

There is NO fallback that fabricates capabilities: both sources missing →
:class:`CapabilitiesMissingError`. Consumers (platform, TUI, agent) must
surface that as "registry unavailable", never as an empty-but-ok catalog.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# Local override written by ``blade-ai capabilities-sync``.
_LOCAL_FILENAME = "skill_capabilities.json"
# Package-bundled registries, one per language.
_BUNDLED_DIRNAME = "_capabilities"


class CapabilitiesError(RuntimeError):
    """Base error for capabilities registry problems."""


class CapabilitiesMissingError(CapabilitiesError):
    """No registry found anywhere — run ``blade-ai capabilities-sync`` or
    upgrade/reinstall the blade-ai package."""


class CapabilitiesStaleError(CapabilitiesError):
    """A locally generated registry exists but the skill cases changed
    after it was generated — re-run ``blade-ai capabilities-sync``."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilityParam:
    """One parameter slot of a capability.

    kind:          target_resource (addresses the victim) | fault_param (tunes the fault)
    resolved_from: environment (filled from the environment config at drill
                   time) | user (filled by the operator, default pre-filled)
    """

    name: str
    kind: str = "fault_param"
    resolved_from: str = "user"
    required: bool = False
    default: str = ""
    description: str = ""


@dataclass(frozen=True)
class CapabilityCase:
    """One executable fault capability distilled from a skill case md."""

    id: str
    family: str                       # k8s | host | python
    profile: str                      # required environment channel profile
    scope: str
    target: str
    action: str
    cluster_scoped: bool
    title: str
    category: str
    symptom: str
    case_path: str
    nl_cmd: str
    params: tuple[CapabilityParam, ...] = field(default_factory=tuple)
    executable: bool = True
    # Back-compat fields for ``blade-ai list`` and other existing consumers.
    use_case_name: str = ""
    inject_kind: str = "blade"
    structured_cmd: str = ""
    direct_cmd: str = ""
    direct_hint: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "family": self.family,
            "profile": self.profile,
            "scope": self.scope,
            "target": self.target,
            "action": self.action,
            "cluster_scoped": self.cluster_scoped,
            "title": self.title,
            "category": self.category,
            "symptom": self.symptom,
            "case_path": self.case_path,
            "nl_cmd": self.nl_cmd,
            "params": [
                {
                    "name": p.name,
                    "kind": p.kind,
                    "resolved_from": p.resolved_from,
                    "required": p.required,
                    "default": p.default,
                    "description": p.description,
                }
                for p in self.params
            ],
            "executable": self.executable,
            "use_case_name": self.use_case_name,
            "inject_kind": self.inject_kind,
            "structured_cmd": self.structured_cmd,
            "direct_cmd": self.direct_cmd,
            "direct_hint": self.direct_hint,
        }


@dataclass
class CapabilitiesRegistry:
    """In-memory view of one language's registry."""

    lang: str
    total: int
    cases: list[CapabilityCase]
    source: str  # file path the registry was read from
    skills_fingerprint: str = ""
    generated_at: str = ""

    def families(self) -> list[str]:
        """Ordered unique family names."""
        seen: dict[str, None] = {}
        for c in self.cases:
            seen.setdefault(c.family, None)
        return list(seen)

    def cases_by_family(self) -> dict[str, list[CapabilityCase]]:
        grouped: dict[str, list[CapabilityCase]] = {}
        for c in self.cases:
            grouped.setdefault(c.family, []).append(c)
        return grouped

    def case_by_id(self, case_id: str) -> CapabilityCase | None:
        for c in self.cases:
            if c.id == case_id:
                return c
        return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_param(raw: dict) -> CapabilityParam:
    return CapabilityParam(
        name=str(raw.get("name", "")),
        kind=str(raw.get("kind", "fault_param")),
        resolved_from=str(raw.get("resolved_from", "user")),
        required=bool(raw.get("required", False)),
        default=str(raw.get("default", "")),
        description=str(raw.get("description", "")),
    )


def _parse_case(raw: dict) -> CapabilityCase:
    params = [_parse_param(p) for p in raw.get("params", []) if isinstance(p, dict) and p.get("name")]
    return CapabilityCase(
        id=str(raw.get("id", "")),
        family=str(raw.get("family", "")),
        profile=str(raw.get("profile", "")),
        scope=str(raw.get("scope", "")),
        target=str(raw.get("target", "")),
        action=str(raw.get("action", "")),
        cluster_scoped=bool(raw.get("cluster_scoped", False)),
        title=str(raw.get("title", "")),
        category=str(raw.get("category", "")),
        symptom=str(raw.get("symptom", raw.get("fault_symptom", ""))),
        case_path=str(raw.get("case_path", raw.get("resource_path", ""))),
        nl_cmd=str(raw.get("nl_cmd", "")),
        params=tuple(params),
        executable=bool(raw.get("executable", True)),
        use_case_name=str(raw.get("use_case_name", "")),
        inject_kind=str(raw.get("inject_kind", "blade")),
        structured_cmd=str(raw.get("structured_cmd", "")),
        direct_cmd=str(raw.get("direct_cmd", "")),
        direct_hint=str(raw.get("direct_hint", "")),
    )


def parse_registry(data: dict, source: str) -> CapabilitiesRegistry:
    """Parse a v2 registry dict. Raises CapabilitiesError on invalid shape."""
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise CapabilitiesError(
            f"Unsupported capabilities schema_version {version!r} in {source}; "
            f"expected {SCHEMA_VERSION}. Re-run `blade-ai capabilities-sync`."
        )
    raw_cases = data.get("cases", [])
    cases = [_parse_case(c) for c in raw_cases if isinstance(c, dict) and c.get("id")]
    return CapabilitiesRegistry(
        lang=str(data.get("lang", "")),
        total=int(data.get("total", len(cases))),
        cases=cases,
        source=source,
        skills_fingerprint=str(data.get("skills_fingerprint", "")),
        generated_at=str(data.get("generated_at", "")),
    )


def probe_registry_file(path: Path) -> tuple[bool, str]:
    """Cheap probe: is this file a v2 registry, and which lang does it hold?

    Returns ``(is_v2, lang)``. Used to skip legacy v1 files and to honour
    locale when deciding whether a local override applies.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, ""
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return False, ""
    return True, str(data.get("lang", ""))


# ---------------------------------------------------------------------------
# Fingerprint (staleness check for locally generated registries)
# ---------------------------------------------------------------------------

def compute_skills_fingerprint() -> str:
    """Combined content fingerprint of every fault-injection skill dir.

    Same content-hash approach as catalog_generator._dir_fingerprint, over
    the skill directories that ``capabilities-sync`` would process, so the
    value is stable across platforms and changes iff case content changes.
    """
    from chaos_agent.skills.catalog_generator import _dir_fingerprint
    from chaos_agent.skills.loader import get_skills_dir
    from chaos_agent.skills.models import SKILL_TYPE_FAULT_INJECTION

    skills_dir = get_skills_dir()
    parts: list[str] = []
    if skills_dir.exists():
        from chaos_agent.skills.loader import load_skill_metadata
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            if not (skill_dir / "references" / "catalogue").exists():
                continue
            try:
                meta = load_skill_metadata(skill_dir)
            except Exception:  # noqa: BLE001
                continue
            if meta.skill_type != SKILL_TYPE_FAULT_INJECTION:
                continue
            parts.append(f"{skill_dir.name}:{_dir_fingerprint(skill_dir)}")
    return hashlib.md5("|".join(parts).encode()).hexdigest() if parts else ""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def bundled_registry_path(locale: str) -> Path:
    """Path of the package-bundled registry for ``locale``."""
    return Path(__file__).resolve().parent.parent / _BUNDLED_DIRNAME / f"capabilities_{locale}.json"


def local_registry_path() -> Path:
    """Path where ``blade-ai capabilities-sync`` writes its output."""
    from chaos_agent.config.settings import settings
    return settings.resolved_memory_dir / _LOCAL_FILENAME


def _read_registry_file(path: Path, validate_staleness: bool) -> CapabilitiesRegistry:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise CapabilitiesMissingError(f"Cannot read capabilities registry {path}: {e}") from e
    registry = parse_registry(data, source=str(path))
    if validate_staleness and registry.skills_fingerprint:
        current = compute_skills_fingerprint()
        if current and current != registry.skills_fingerprint:
            raise CapabilitiesStaleError(
                f"Capabilities registry {path} is stale: skill cases changed after it was "
                f"generated. Re-run `blade-ai capabilities-sync`."
            )
    return registry


def load_capabilities(locale: str = "zh", *, path: str | Path | None = None) -> CapabilitiesRegistry:
    """Load the capabilities registry — the single source of capability truth.

    Resolution: explicit path > local sync output (v2 + matching lang only,
    staleness-checked) > package-bundled ``capabilities_{locale}.json``
    > CapabilitiesMissingError. Never fabricates data.
    """
    if path is not None:
        return _read_registry_file(Path(path), validate_staleness=False)

    local = local_registry_path()
    if local.exists():
        is_v2, lang = probe_registry_file(local)
        # A locally generated registry is language-specific: it only counts
        # as the override for the locale it was generated for (legacy v1
        # files and other locales fall through to the bundled registry).
        if is_v2 and lang == locale:
            try:
                return _read_registry_file(local, validate_staleness=True)
            except CapabilitiesStaleError:
                raise
            except CapabilitiesError as e:
                logger.warning("Local capabilities file unusable (%s); trying bundled registry", e)

    bundled = bundled_registry_path(locale)
    if bundled.exists():
        return _read_registry_file(bundled, validate_staleness=False)

    raise CapabilitiesMissingError(
        "No capabilities registry found: no usable local "
        f"{local} and no bundled {bundled}. "
        "Run `blade-ai capabilities-sync` or upgrade/reinstall the blade-ai package."
    )
