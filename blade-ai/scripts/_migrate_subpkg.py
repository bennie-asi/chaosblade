#!/usr/bin/env python3
"""S-phase mechanical migration helper.

Moves a set of modules from ``src/chaos_agent/agent/nodes/`` into a subpackage
``src/chaos_agent/agent/nodes/<subpkg>/`` and rewrites every reference across
``src`` and ``tests``:

  * dotted form  ``chaos_agent.agent.nodes.<mod>``  (imports, mock.patch targets, comments)
  * slash form   ``src/chaos_agent/agent/nodes/<mod>.py`` (source-path guard tests)

ZERO logic change: only path/import strings are touched. Files are moved with
``git mv`` so history is preserved.

Usage:
    python scripts/_migrate_subpkg.py <base_pkg_path> <subpkg> <mod1> <mod2> ...

    <base_pkg_path> is the dotted package that currently holds the modules,
    e.g. ``chaos_agent.agent.nodes`` or ``chaos_agent.agent``.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [PROJECT_ROOT / "src", PROJECT_ROOT / "tests"]


def dotted_to_relpath(dotted: str) -> str:
    """chaos_agent.agent.nodes -> src/chaos_agent/agent/nodes"""
    return "src/" + dotted.replace(".", "/")


def rewrite_text(text: str, base_dotted: str, subpkg: str, mods: list[str]) -> str:
    base_slash = dotted_to_relpath(base_dotted)
    for mod in mods:
        # Dotted: base.mod (not followed by an identifier char) -> base.subpkg.mod
        text = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(base_dotted)}\.{re.escape(mod)}(?![A-Za-z0-9_])",
            f"{base_dotted}.{subpkg}.{mod}",
            text,
        )
        # ``from base import mod`` (submodule-name import) -> ``from base.subpkg import mod``
        text = re.sub(
            rf"(?<![A-Za-z0-9_])from {re.escape(base_dotted)} import {re.escape(mod)}(?![A-Za-z0-9_])",
            f"from {base_dotted}.{subpkg} import {mod}",
            text,
        )
        # Slash path: base_slash/mod.py -> base_slash/subpkg/mod.py
        text = text.replace(
            f"{base_slash}/{mod}.py",
            f"{base_slash}/{subpkg}/{mod}.py",
        )
    return text


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    base_dotted = sys.argv[1]
    subpkg = sys.argv[2]
    mods = sys.argv[3:]

    base_slash = dotted_to_relpath(base_dotted)
    base_dir = PROJECT_ROOT / base_slash
    sub_dir = base_dir / subpkg

    # 1. Create subpackage dir + empty __init__.py (zero import-time logic).
    sub_dir.mkdir(exist_ok=True)
    init_py = sub_dir / "__init__.py"
    if not init_py.exists():
        init_py.write_text(
            f'"""Subpackage {subpkg} (S-phase structural split; no logic).""" \n',
            encoding="utf-8",
        )

    # 2. git mv each module file.
    for mod in mods:
        src_file = base_dir / f"{mod}.py"
        dst_file = sub_dir / f"{mod}.py"
        if not src_file.exists():
            print(f"  SKIP (missing): {src_file}")
            continue
        subprocess.run(
            ["git", "mv", str(src_file), str(dst_file)],
            cwd=PROJECT_ROOT, check=True,
        )
        print(f"  moved: {mod}.py -> {subpkg}/{mod}.py")

    # 3. Rewrite references across src + tests.
    changed = 0
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            original = path.read_text(encoding="utf-8")
            updated = rewrite_text(original, base_dotted, subpkg, mods)
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                changed += 1
    print(f"  rewrote references in {changed} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
