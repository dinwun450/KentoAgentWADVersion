from __future__ import annotations

import os
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Any, Literal, cast

import weave

WeaveMode = Literal["auto", "local", "required"]


class ObservabilityConfigurationError(RuntimeError):
    """Raised when production-required Weave observability cannot be initialized."""


@dataclass(frozen=True)
class WeaveSettings:
    """Runtime policy for Weave without putting credentials in application state."""

    mode: WeaveMode = "auto"
    project: str | None = None
    environment: str = "development"

    @classmethod
    def from_env(cls) -> WeaveSettings:
        raw_mode = os.getenv("KENTO_WEAVE_MODE", "auto").lower()
        if raw_mode not in {"auto", "local", "required"}:
            raise ObservabilityConfigurationError(
                "KENTO_WEAVE_MODE must be one of: auto, local, required"
            )
        entity = os.getenv("WANDB_ENTITY")
        project = os.getenv("WANDB_PROJECT") or os.getenv("WEAVE_PROJECT")
        project_ref = (
            f"{entity}/{project}" if entity and project and "/" not in project else project
        )
        return cls(
            mode=cast(WeaveMode, raw_mode),
            project=project_ref,
            environment=os.getenv("KENTO_ENVIRONMENT", "development"),
        )


_client_lock = Lock()
_weave_clients: dict[str, Any] = {}
logger = logging.getLogger(__name__)


def initialize_weave(settings: WeaveSettings | None = None) -> Any | None:
    """Initialize one Weave client per project, with explicit fail-open/fail-fast policy."""

    resolved = settings or WeaveSettings.from_env()
    if resolved.mode == "local":
        return None
    if not resolved.project:
        if resolved.mode == "required":
            raise ObservabilityConfigurationError(
                "WANDB_PROJECT (and normally WANDB_ENTITY) is required in production"
            )
        return None
    with _client_lock:
        if resolved.project in _weave_clients:
            return _weave_clients[resolved.project]
        try:
            client = weave.init(resolved.project)
        except Exception as exc:
            if resolved.mode == "required":
                raise ObservabilityConfigurationError(
                    f"Could not initialize required Weave project {resolved.project!r}"
                ) from exc
            return None
        _weave_clients[resolved.project] = client
        return client


@weave.op(name="kentoagent.simulation_event", enable_code_capture=False)
def publish_event(record: dict[str, Any]) -> dict[str, Any]:
    """Create a searchable child call for a typed domain event."""

    return record


def orchestration_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Keep world snapshots and mutable orchestrator internals out of trace inputs."""

    orchestrator = inputs.get("self")
    world = getattr(orchestrator, "world", None)
    config = getattr(orchestrator, "config", None)
    return {
        "run_id": getattr(world, "run_id", None),
        "scenario_id": getattr(world, "scenario_id", None),
        "seed": getattr(world, "seed", None),
        "step": getattr(world, "step", None),
        "max_steps": getattr(config, "max_steps", None),
        "requested_steps": inputs.get("steps"),
    }


def inference_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Trace inference context without uploading the full world or hidden reasoning."""

    request = inputs.get("request")
    world = getattr(request, "world", None)
    role = getattr(request, "role", None)
    return {
        "run_id": getattr(world, "run_id", None),
        "scenario_id": getattr(world, "scenario_id", None),
        "step": getattr(world, "step", None),
        "agent_role": getattr(role, "value", role),
        "provider": getattr(request, "provider", None),
        "model": getattr(request, "model", None),
        "prior_decision_count": len(getattr(request, "prior_decisions", [])),
        "validation_feedback": list(getattr(request, "validation_feedback", []))[:5],
    }


class TraceSink:
    """Local replay-oriented records mirrored into causal Weave operations.

    Domain events remain the source of replay truth. In ``auto`` mode a missing or
    unavailable Weave project fails open for local development. ``required`` mode
    fails fast and is intended for production workers and API processes.
    """

    def __init__(self, settings: WeaveSettings | None = None) -> None:
        self.records: list[dict[str, Any]] = []
        self.settings = settings or WeaveSettings.from_env()
        self.client = initialize_weave(self.settings)
        if self.client is None and self.settings.project and self.settings.mode == "auto":
            self.records.append(
                {
                    "event": "weave_unavailable",
                    "project": self.settings.project,
                    "status": "degraded",
                }
            )

    @property
    def weave_enabled(self) -> bool:
        return self.client is not None

    def emit(self, event: str, **dimensions: Any) -> None:
        record = {
            "event": event,
            "environment": self.settings.environment,
            **dimensions,
        }
        self.records.append(record)
        if self.client is not None:
            try:
                publish_event(record)
            except Exception:
                logger.warning("Weave telemetry export failed", exc_info=True)

    @contextmanager
    def span(self, name: str, **dimensions: Any) -> Iterator[None]:
        started = perf_counter()
        self.emit("span_started", name=name, **dimensions)
        status = "ok"
        try:
            yield
        except Exception:
            status = "error"
            raise
        finally:
            self.emit(
                "span_finished",
                name=name,
                status=status,
                latency_ms=round((perf_counter() - started) * 1000, 3),
                **dimensions,
            )


def observability_status(settings: WeaveSettings | None = None) -> Mapping[str, Any]:
    resolved = settings or WeaveSettings.from_env()
    return {
        "mode": resolved.mode,
        "project": resolved.project,
        "environment": resolved.environment,
        "configured": bool(resolved.project),
    }
