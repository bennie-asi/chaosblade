"""Connection field pass-through from the L4 ``conn`` dict into graph state.

Regression cover for the host-channel skill mis-selection bug: the three
``graph_input`` branches in ``l4/interaction.py`` used to carry a copy-pasted
list of only the four Kubernetes fields, silently dropping every host-channel
field. The intent graph then saw ``kube_connection_mode = None`` and could not
tell a host environment from a K8s one — so it loaded ``k8s-chaos-skills`` for
SSH / KubeWiz-host environments.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from chaos_agent.l4.events import _conn_to_state_patch
from chaos_agent.l4.interaction import _conn_state_fields

# All connection fields the graph state expects. Kept explicit here so the
# test fails loudly if the mapping ever loses a field again.
_EXPECTED_KEYS = {
    "kubeconfig",
    "kube_context",
    "kubewiz_cluster_uuid",
    "kubewiz_profile",
    "kube_connection_mode",
    "host_name",
    "ssh_host",
    "ssh_user",
    "ssh_port",
}


def test_maps_all_connection_fields():
    assert set(_conn_state_fields({})) == _EXPECTED_KEYS


def test_host_channel_fields_survive():
    """The whole point of the fix: host-channel fields reach the state."""
    conn = {
        "kube_connection_mode": "ssh",
        "ssh_host": "10.0.0.5",
        "ssh_user": "root",
        "ssh_port": 22,
    }
    out = _conn_state_fields(conn)
    assert out["kube_connection_mode"] == "ssh"
    assert out["ssh_host"] == "10.0.0.5"
    assert out["ssh_user"] == "root"
    assert out["ssh_port"] == 22


def test_empty_conn_produces_no_garbage():
    out = _conn_state_fields({})
    # string fields default to "", the numeric port to None — never a stray
    # value that would look like a real configured channel.
    assert out["kube_connection_mode"] == ""
    assert out["ssh_host"] == ""
    assert out["ssh_port"] is None


def test_ssh_port_stays_numeric():
    assert _conn_state_fields({"ssh_port": 2222})["ssh_port"] == 2222
    # falsy / missing → None (not 0, not "")
    assert _conn_state_fields({"ssh_port": 0})["ssh_port"] is None
    assert _conn_state_fields({})["ssh_port"] is None


def test_three_graph_input_branches_share_one_field_source():
    """No branch may hand-roll its own connection-field list.

    Enforces the single-source rule: every ``graph_input`` literal must splat
    ``_conn_state_fields(conn)`` and must NOT inline ``conn.get("kubeconfig"...``
    again (which is how the three branches drifted before).
    """
    src = Path(
        inspect.getfile(_conn_state_fields)
    ).read_text(encoding="utf-8")

    # The only place a raw ``conn.get("kubeconfig"...`` literal may appear is
    # inside the helper itself. Count occurrences in executable code.
    tree = ast.parse(src)
    helper_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_conn_state_fields":
            helper_lines = set(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    offending = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "kubeconfig"
            and node.lineno not in helper_lines
        ):
            offending.append(node.lineno)
    assert not offending, (
        f"connection fields inlined outside _conn_state_fields at lines {offending} "
        "— branches will drift again"
    )

    # And the splat is used at least three times (the three branches).
    splat_uses = src.count("**_conn_state_fields(conn)")
    assert splat_uses >= 3, f"expected 3 branches to splat the helper, found {splat_uses}"


class TestConnToStatePatch:
    """``update_connection`` uses the same field set as ``clarify``.

    The original fix (67ab08e) covered only the three ``graph_input``
    branches; ``_conn_to_state_patch`` kept the old four-field list, so a
    mid-conversation channel switch silently dropped every host-channel
    field again. These tests pin the two sources to the same field set.
    """

    def test_patch_covers_every_state_field(self):
        """Every field _conn_state_fields emits must be patchable."""
        conn = {
            "kubeconfig": "cfg",
            "kube_context": "ctx",
            "kubewiz_cluster_uuid": "u",
            "kubewiz_profile": "p",
            "kube_connection_mode": "ssh",
            "host_name": "h1",
            "ssh_host": "10.0.0.9",
            "ssh_user": "root",
            "ssh_port": 2222,
        }
        patch = _conn_to_state_patch(conn)
        assert set(patch.keys()) == _EXPECTED_KEYS
        assert patch["kube_connection_mode"] == "ssh"
        assert patch["ssh_host"] == "10.0.0.9"
        assert patch["ssh_port"] == 2222

    def test_patch_only_includes_present_keys(self):
        """Patch semantics: absent keys stay out (no clobber with '')."""
        patch = _conn_to_state_patch({"kubeconfig": "cfg"})
        assert patch == {"kubeconfig": "cfg"}
        assert "ssh_host" not in patch

    def test_patch_ssh_port_stays_numeric(self):
        assert _conn_to_state_patch({"ssh_port": 2222})["ssh_port"] == 2222
        # falsy value present → None (never "", matching _conn_state_fields)
        assert _conn_to_state_patch({"ssh_port": 0})["ssh_port"] is None

    def test_patch_and_fields_never_drift_on_value_semantics(self):
        """For every present key both mappings must agree on the value."""
        conn = {
            "kubeconfig": "cfg",
            "kube_connection_mode": "kubewiz_host",
            "host_name": "node-a",
            "ssh_host": "10.0.0.1",
            "ssh_user": "admin",
            "ssh_port": 22,
        }
        patch = _conn_to_state_patch(conn)
        fields = _conn_state_fields(conn)
        for key, value in patch.items():
            assert fields[key] == value, f"drift on {key!r}: patch={value!r} fields={fields[key]!r}"
