from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from kentoagent.config import SimulationConfig
from kentoagent.durable.client import connect_temporal, task_queue
from kentoagent.durable.models import (
    DurableSimulationInput,
    DurableSimulationStatus,
    LLMProvider,
    StepEvaluationSettings,
)
from kentoagent.durable.workflow import KentoAgentParallelWorkflow
from kentoagent.evaluation.metrics import calculate_metrics
from kentoagent.simulation.entities import WorldState
from kentoagent.simulation.geojson import world_to_geojson
from kentoagent.simulation.scenario import ScenarioConfig

router = APIRouter(prefix="/api/durable", tags=["durable-agent-system"])
_client: Client | None = None
_client_lock = asyncio.Lock()


class DurableStartRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    seed: int = 49281
    survivor_count: int = Field(default=8, ge=1, le=25)
    agent_count: int = Field(default=5, ge=5, le=25)
    hazard_density: float = Field(default=0.18, ge=0, le=0.8)
    blocked_road_probability: float = Field(default=0.16, ge=0, le=0.8)
    max_steps: int = Field(default=100, ge=1, le=10_000)
    model: str = "gpt-4.1-mini"
    provider: LLMProvider = "openai"
    step_interval_s: float = Field(default=1.0, ge=0, le=60)
    evaluation: StepEvaluationSettings = Field(default_factory=StepEvaluationSettings.from_env)


class DurableControlRequest(BaseModel):
    command_id: str | None = Field(default=None, min_length=1, max_length=200)
    expected_state_version: int | None = Field(default=None, ge=1)


def _business_id(request: DurableStartRequest) -> str:
    if request.idempotency_key:
        return request.idempotency_key
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"request-{hashlib.sha256(encoded).hexdigest()}"


def _request_fingerprint(request: DurableStartRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _workflow_id(business_id: str) -> str:
    digest = hashlib.sha256(business_id.encode()).hexdigest()[:32]
    return f"kentoagent-v2-{digest}"


async def temporal_client() -> Client:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                try:
                    _client = await connect_temporal()
                except RPCError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="Temporal is unavailable; start it with: temporal server start-dev",
                    ) from exc
    return _client


@router.post("/runs", status_code=202)
async def start_run(request: DurableStartRequest) -> dict[str, str]:
    client = await temporal_client()
    business_id = _business_id(request)
    request_fingerprint = _request_fingerprint(request)
    workflow_id = _workflow_id(business_id)
    input = DurableSimulationInput(
        business_id=business_id,
        request_fingerprint=request_fingerprint,
        workflow_id=workflow_id,
        scenario=ScenarioConfig(
            seed=request.seed,
            survivor_count=request.survivor_count,
            agent_count=request.agent_count,
            hazard_density=request.hazard_density,
            blocked_road_probability=request.blocked_road_probability,
        ),
        simulation=SimulationConfig(max_steps=request.max_steps),
        model=request.model,
        provider=request.provider,
        step_interval_s=request.step_interval_s,
        evaluation=request.evaluation,
    ).model_dump(mode="json")
    attached = False
    try:
        handle = await client.start_workflow(
            KentoAgentParallelWorkflow.run,
            input,
            id=workflow_id,
            task_queue=task_queue(),
            id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError as exc:
        attached = True
        handle = client.get_workflow_handle(workflow_id)
        existing_status = await handle.query(KentoAgentParallelWorkflow.status)
        existing_fingerprint = str(existing_status.get("request_fingerprint", ""))
        if existing_fingerprint and existing_fingerprint != request_fingerprint:
            raise HTTPException(
                status_code=409,
                detail="idempotency key is already bound to a different request",
            ) from exc
    return {
        "workflow_id": workflow_id,
        "run_id": handle.first_execution_run_id or "",
        "business_id": business_id,
        "attached": str(attached).lower(),
    }


@router.get("/runs/{workflow_id}")
async def run_status(workflow_id: str) -> dict[str, Any]:
    client = await temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    result = await handle.query(KentoAgentParallelWorkflow.status)
    status = DurableSimulationStatus.model_validate(result)
    payload = status.model_dump(mode="json")
    if status.world is not None:
        world = WorldState.model_validate(status.world)
        payload["state"] = {
            "world": world.model_dump(mode="json"),
            "geojson": world_to_geojson(world),
            "metrics": calculate_metrics(world).model_dump(mode="json"),
        }
    else:
        payload["state"] = None
    return payload


@router.post("/runs/{workflow_id}/{command}")
async def control_run(
    workflow_id: str,
    command: str,
    request: DurableControlRequest | None = None,
) -> dict[str, str]:
    if command not in {"pause", "resume", "stop"}:
        raise HTTPException(status_code=400, detail="command must be pause, resume, or stop")
    client = await temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    command_id = (request.command_id if request else None) or f"{workflow_id}:{command}"
    expected_version = request.expected_state_version if request else None
    signal = {
        "pause": KentoAgentParallelWorkflow.pause,
        "resume": KentoAgentParallelWorkflow.resume,
        "stop": KentoAgentParallelWorkflow.stop,
    }[command]
    await handle.signal(signal, args=[command_id, expected_version])
    return {"workflow_id": workflow_id, "command": command, "command_id": command_id}
