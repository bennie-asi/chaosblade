"""Single source of truth for chaos task identity.

Only the **inject** and **recover** pipelines own the concept of a
"task".  Intent clarification, chat, and capability Q&A do not — they
are conversation turns, not tasks (see
``agent.nodes.planning.intent_clarification._allocate_operation_task_id``
for the original statement of this contract).

Consequently a real task identity is *minted* in one of two formats —
``inject-<uuid4>`` for the inject pipeline and ``recover-<uuid4>`` for
the recover pipeline (the prefix makes the task kind visible directly
in the persisted json filenames) — and everything else (LangGraph
thread ids such as ``chaos-<session>``, per-turn ids such as
``turn-<hex>``, placeholder strings such as ``"unknown"``, or an absent
value) means **"there is no task"**.

The legacy ``task-<uuid4>`` prefix predates the split; ids minted that
way remain valid forever (old records and their cross-references must
keep resolving), but nothing mints them anymore.

Why this module exists
----------------------
The persistence layer used to guard its writes with a *blacklist*
(``task_id.startswith("turn-")``).  A blacklist only rejects the dirty
ids it happens to know about, so any new caller passing a non-task
string silently created a bogus ``tasks`` row — the platform's
``chaos-<session>`` thread id did exactly that, producing "ghost"
experiments that surfaced in the recover flow with no injection state
to roll back.

The guards therefore use a **whitelist** built on :func:`is_real_task_id`,
and this module is its only home: callers must not re-implement the
``task-`` prefix check locally (the fix for the ``namespace`` validator
was needed twice precisely because that judgement had been hardcoded in
several places).
"""

import uuid

# Legacy prefix (pre-split). Still accepted by the whitelist so old
# persisted records keep resolving; never minted anymore.
TASK_ID_PREFIX = "task-"

# Per-pipeline prefixes minted today.
INJECT_TASK_ID_PREFIX = "inject-"
RECOVER_TASK_ID_PREFIX = "recover-"

# Every prefix that denotes a real, persistable task identity.
TASK_ID_PREFIXES = (TASK_ID_PREFIX, INJECT_TASK_ID_PREFIX, RECOVER_TASK_ID_PREFIX)

__all__ = [
    "TASK_ID_PREFIX",
    "INJECT_TASK_ID_PREFIX",
    "RECOVER_TASK_ID_PREFIX",
    "TASK_ID_PREFIXES",
    "is_real_task_id",
    "new_task_id",
    "new_inject_task_id",
    "new_recover_task_id",
]


def is_real_task_id(task_id: object) -> bool:
    """Return ``True`` only for a real, persistable task identity.

    A real task id is a non-empty ``str`` starting with one of
    :data:`TASK_ID_PREFIXES`.  Everything else — ``None``, non-``str``
    values, ``""``, conversation thread ids (``chaos-…``), per-turn ids
    (``turn-…``) and placeholders (``"unknown"``) — means *no task*, and
    must never reach the ``tasks`` / ``task_details`` / ``task_spans``
    tables.
    """
    return isinstance(task_id, str) and task_id.startswith(TASK_ID_PREFIXES)


def new_inject_task_id() -> str:
    """Mint a fresh identity for an **inject** task (``inject-<uuid4>``)."""
    return f"{INJECT_TASK_ID_PREFIX}{uuid.uuid4()}"


def new_recover_task_id() -> str:
    """Mint a fresh identity for a **recover** task (``recover-<uuid4>``)."""
    return f"{RECOVER_TASK_ID_PREFIX}{uuid.uuid4()}"


def new_task_id() -> str:
    """Mint a fresh real task identity (inject flavour).

    Called at the moment a dialogue transitions into the inject or
    recover pipeline, so the task identity is born inside the pipeline
    that owns it.  Recover callers should prefer
    :func:`new_recover_task_id`; this alias keeps historical call sites
    that pre-date the prefix split working unchanged.
    """
    return new_inject_task_id()
