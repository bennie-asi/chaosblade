"""CLI command: blade-ai capabilities-sync"""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from chaos_agent.cli.output import OutputFormat, format_output
from chaos_agent.config.settings import settings
from chaos_agent.preflight import LIST_CHECKS, run_command

logger = logging.getLogger(__name__)


def _get_output_path() -> Path:
    return settings.resolved_memory_dir / "skill_capabilities.json"


def _discover_catalogue_roots() -> dict[str, Path]:
    """Find every fault-injection skill that ships a case catalogue.

    Data-driven discovery (scan + metadata filter) instead of a hardcoded
    skill name, so newly added fault skills (host / python-app / future
    families) are picked up without touching this command.
    """
    from chaos_agent.skills.loader import get_skills_dir, load_skill_metadata
    from chaos_agent.skills.models import SKILL_TYPE_FAULT_INJECTION

    roots: dict[str, Path] = {}
    skills_dir = get_skills_dir()
    if not skills_dir.exists():
        return roots
    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        catalogue = skill_dir / "references" / "catalogue"
        if not catalogue.exists():
            continue
        try:
            meta = load_skill_metadata(skill_dir)
        except Exception as e:
            logger.warning("Skipping skill dir %s: %s", skill_dir.name, e)
            continue
        if meta.skill_type != SKILL_TYPE_FAULT_INJECTION:
            logger.info(
                "Skipping skill '%s' (skill_type=%s, not fault-injection)",
                meta.name, meta.skill_type,
            )
            continue
        roots[meta.name] = catalogue
    return roots


def capabilities_sync(
    output: OutputFormat = typer.Option(OutputFormat.json, "--output", "-o", help="Output format: json|yaml"),
    lang: str = typer.Option("en", "--lang", "-l", help="Description language for nl_cmd/fault_symptom: en (default) | cn"),
    reference: Path | None = typer.Option(
        None, "--reference", "-r",
        help="Reference v2 registry (usually the other language's artifact) to anchor "
             "structural fields (triple/params schema), keeping both languages id-isomorphic",
    ),
):
    """Sync skill capabilities: LLM derives commands from each skill case.

    This is a slow command — it calls the LLM for every catalogue case. The
    case library is the generation source; no blade binary probing happens.
    Run it when you add/remove/edit skill cases. When generating the second
    language, pass --reference <first-language artifact> so the structural
    fields stay anchored and both registries keep identical case ids.
    """
    if lang.strip().lower() not in ("en", "cn"):
        raise typer.BadParameter(f"Unsupported --lang {lang!r}; expected en or cn")
    if reference is not None and not reference.exists():
        raise typer.BadParameter(f"--reference file not found: {reference}")

    async def _local(backend):
        from chaos_agent.agent.factory import make_llm
        from chaos_agent.skills.case_sync import sync_capabilities

        catalogue_roots = _discover_catalogue_roots()

        if not catalogue_roots:
            return {
                "status": "error",
                "code": 1,
                "message": "No fault-injection skill with references/catalogue/ found",
            }

        # Thinking stays ON here (unlike display-catalog / postmortem
        # generation): this pass derives the actual inject/verify/recover
        # commands from each case doc, and those commands feed the
        # injection chain directly — derivation quality beats latency.
        llm = make_llm(read_timeout=120, enable_thinking=True)
        out_path = _get_output_path()

        typer.echo(
            f"Syncing capabilities for {len(catalogue_roots)} skill(s): "
            f"{', '.join(sorted(catalogue_roots))} (this may take a minute)...",
            err=True,
        )
        catalog = await sync_capabilities(
            catalogue_roots, llm, out_path, lang=lang, reference_registry=reference,
        )

        blade_count = sum(1 for c in catalog["cases"] if c["inject_kind"] == "blade")
        kubectl_count = sum(1 for c in catalog["cases"] if c["inject_kind"] == "kubectl")
        mixed_count = sum(1 for c in catalog["cases"] if c["inject_kind"] == "mixed")

        typer.echo(
            f"\n✓ Sync complete: {catalog['total']} cases "
            f"(blade={blade_count}, kubectl={kubectl_count}, mixed={mixed_count})\n"
            f"  Written to: {out_path}",
            err=True,
        )

        return {
            "status": "success",
            "code": 0,
            "message": "success",
            "data": {
                "total": catalog["total"],
                "skills": sorted(catalogue_roots),
                "blade_count": blade_count,
                "kubectl_count": kubectl_count,
                "mixed_count": mixed_count,
                "output_path": str(out_path),
            },
        }

    async def _server(backend):
        return await backend.post("/api/v1/capabilities/sync", {})

    result = run_command(LIST_CHECKS, _local, _server)
    typer.echo(format_output(result, output))


