"""Parametrized entry for tests/golden/scenarios/*.yaml (T1.14 Phase 0).

Every YAML becomes one pytest case marked ``golden``. CI runs them with the
full suite (``pytest -q``); real-model nightly runs (Phase 1, win-rate
matrix) reuse the same corpus through golden_runner directly.
"""

import pytest

from golden_runner import load_scenarios, run_scenario

scenarios = load_scenarios()


@pytest.mark.golden
@pytest.mark.parametrize("scenario", scenarios, ids=[s["name"] for s in scenarios])
async def test_golden_scenario(scenario: dict) -> None:
    failures = await run_scenario(scenario)
    assert not failures, f"golden scenario {scenario['name']!r} failed:\n" + "\n".join(
        failures
    )
