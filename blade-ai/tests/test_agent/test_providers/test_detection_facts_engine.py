"""Face 8 (design doc 4.6): host-native injection-vs-diagnostic attribution.

``_host_native_call_is_readonly`` decides whether a successful host-carrier
tool call was a read-only diagnostic (not an injection). Since the engine
flip the command-string form is judged on structural facts — the ONLY
engine; the legacy shlex token-soup path was deleted with it — and the
binary+args form gained the ``watch`` sh -c re-parse check it never had.
Each divergence below was measured under both engines before the flip
(adjudication §9: all tightenings or hole closures; the dangerous
MUTATES→readonly direction does not occur).
"""

from chaos_agent.agent.providers.message_scanning import _host_native_call_is_readonly


class TestHostNativeAttributionFacts:
    def test_pipe_is_structure_not_argv(self):
        # Legacy shlex delivered ``|`` as an inert argv word and the df rule
        # waved it through; the real shell builds a pipeline.
        assert not _host_native_call_is_readonly({"command": "df -h | grep /"})

    def test_chain_is_structure(self):
        assert not _host_native_call_is_readonly(
            {"command": "cat /etc/passwd; rm -rf /"}
        )

    def test_watch_payload_reparsed_command_form(self):
        assert not _host_native_call_is_readonly({"command": "watch echo '$(id)'"})

    def test_watch_payload_reparsed_argv_form(self):
        # The binary+args form never had a substring screen — this hole let a
        # ``watch``-wrapped injection be mis-attributed as a diagnostic.
        assert not _host_native_call_is_readonly(
            {"binary": "watch", "args": ["echo", "$(id)"]}
        )

    # --- no-drift pins ---
    def test_plain_diagnostic_unchanged(self):
        assert _host_native_call_is_readonly({"command": "df -h"})

    def test_mutation_unchanged(self):
        assert not _host_native_call_is_readonly({"command": "rm -rf /"})
