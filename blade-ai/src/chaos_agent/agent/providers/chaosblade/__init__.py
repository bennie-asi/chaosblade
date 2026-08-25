"""ChaosBlade carrier family — the CLI carrier and its python-agent sibling.

Two registered providers (``provider.ChaosbladeProvider`` for the blade CLI,
``python_provider.ChaosbladePythonProvider`` for the python-agent protocol)
plus the family-shared detection / verify / recover facilities they both
build on. External consumers import the concrete modules directly — this
package ``__init__`` intentionally re-exports nothing.
"""
