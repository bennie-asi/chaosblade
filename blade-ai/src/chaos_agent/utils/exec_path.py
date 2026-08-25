"""Carrier-agnostic executable path resolution.

Phase-9 T3.2: ``resolve_exec_path``/``is_executable`` moved here from
``utils/blade_paths.py`` (deleted). The blade-binary lookup that shared
that file (``get_bundled_blade_path``) is carrier knowledge and moved to
``providers/chaosblade/declaration.py`` (phase-12) instead; these two
helpers serve generic command resolution (kubectl / git / wiz / shell
tool execution) and stay in utils so those consumers never depend on a
carrier module.
"""

import os
import shutil


def is_executable(cmd: str) -> bool:
    """Return True if ``cmd`` resolves to a usable executable.

    ``cmd`` may be either shape a binary resolver returns:
      - a full / relative path (bundled, runtime vendor, repo, configured)
      - a bare command name to resolve on PATH

    These need different checks, and crucially they must NOT be conflated
    by passing a path to ``shutil.which``. On Windows before Python 3.12,
    ``shutil.which`` mishandles a ``cmd`` that contains a directory
    component and returns ``None`` even for a valid executable — and the
    project's ``requires-python = ">=3.11"`` puts 3.11-on-Windows in scope.
    So: anything with a directory component is checked as a file directly;
    only a bare name goes through ``shutil.which``.

    Shared by the blade and kubectl presence checks (preflight) and the
    runtime blade-availability probe (chaosblade_installer).
    """
    if not cmd:
        return False
    if os.path.dirname(cmd):
        # Has a path component → check the file directly, never via which().
        return os.path.isfile(cmd)
    # Bare command name → PATH lookup (no directory component, Windows-safe).
    return shutil.which(cmd) is not None


def resolve_exec_path(cmd_name: str) -> str:
    """Resolve a command name to an absolute path for posix_spawn.

    If *cmd_name* already has a directory component (absolute or relative
    path), return it unchanged — posix_spawn will use it directly.

    Otherwise resolve via PATH lookup (``shutil.which``). If the binary
    cannot be found, return the original bare name so the caller still
    gets a meaningful error (FileNotFoundError) rather than passing
    ``None`` to ``create_subprocess_exec``.

    Purpose: ensure ``asyncio.create_subprocess_exec`` uses
    ``posix_spawn`` instead of ``fork()`` on Linux. ``fork()`` triggers
    APM native ``pthread_atfork`` callbacks (uniagent) which crash the
    worker.
    """
    if not cmd_name:
        return cmd_name
    if os.path.dirname(cmd_name):
        return cmd_name  # Already has path component
    resolved = shutil.which(cmd_name)
    return resolved or cmd_name
