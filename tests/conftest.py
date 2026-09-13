from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_remote_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit and CI tests must never publish traces, evaluations, or LLM calls."""

    monkeypatch.setenv("KENTO_WEAVE_MODE", "local")
    monkeypatch.setenv("WANDB_MODE", "offline")
