from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

from kentoagent.api.durable import router as durable_router
from kentoagent.api.service import GenerateRequest, OperatorCommand, SimulationService
from kentoagent.evaluation.metrics import calculate_metrics
from kentoagent.observability.tracing import observability_status
from kentoagent.simulation.geojson import world_to_geojson
from kentoagent.simulation.geojson_bridge import entities_to_geojson
from kentoagent.storage.scenario_provider import MapEntity
from kentoagent.storage.snowflake_loader import ScenarioLoadConfig, load_to_snowflake, _get_connection

app = FastAPI(title="KentoAgent", version="0.1.0")
app.include_router(durable_router)
service = SimulationService()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "observability": observability_status()}


@app.get("/api/state")
def state() -> dict[str, Any]:
    return {
        "world": service.world.model_dump(mode="json"),
        "geojson": world_to_geojson(service.world),
        "metrics": calculate_metrics(service.world).model_dump(mode="json"),
    }


def _snowflake_layers(seed: int) -> dict[str, Any]:
    """Build survivor/blockage GeoJSON from the raw Snowflake tables."""
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT survivor_id AS entity_id, 'survivor' AS entity_type,
                       seed, latitude, longitude, injury_severity AS severity,
                       visible, trapped, status, generation_version
                FROM survivors
                WHERE seed = %s
                UNION ALL
                SELECT blockage_id AS entity_id, 'blockage' AS entity_type,
                       seed, latitude, longitude, severity,
                       NULL AS visible, NULL AS trapped, NULL AS status,
                       generation_version
                FROM blockages
                WHERE seed = %s AND active = TRUE
                ORDER BY entity_type, entity_id
                """,
                (seed, seed),
            )
            columns = [desc[0].lower() for desc in cur.description]
            entities = [
                MapEntity(
                    entity_id=row[columns.index("entity_id")],
                    entity_type=row[columns.index("entity_type")],
                    seed=row[columns.index("seed")],
                    latitude=row[columns.index("latitude")],
                    longitude=row[columns.index("longitude")],
                    severity=row[columns.index("severity")] or "unknown",
                    visible=row[columns.index("visible")],
                    trapped=row[columns.index("trapped")],
                    status=row[columns.index("status")],
                )
                for row in cur.fetchall()
            ]
        finally:
            conn.close()

        survivors = [e for e in entities if e.entity_type == "survivor"]
        blockages = [e for e in entities if e.entity_type == "blockage"]
        logger.info("Snowflake layers for seed %s: %d survivors, %d blockages", seed, len(survivors), len(blockages))
        return {
            "survivor-layer": entities_to_geojson(survivors),
            "blockage-layer": entities_to_geojson(blockages),
        }
    except Exception:
        logger.exception("Failed to read Snowflake layers for seed %s", seed)
        return {}


@app.post("/api/scenarios")
def generate(request: GenerateRequest) -> dict[str, Any]:
    service.generate(request)

    try:
        load_to_snowflake(ScenarioLoadConfig(seed=request.seed))
    except Exception:
        logger.exception("Failed to load seed %s into Snowflake", request.seed)

    result = state()
    sf_layers = _snowflake_layers(request.seed)
    result["geojson"].update(sf_layers)
    return result


@app.post("/api/simulation/step")
def step() -> dict[str, Any]:
    service.step()
    return state()


@app.post("/api/simulation/play")
def play() -> dict[str, Any]:
    service.world.running = True
    return state()


@app.post("/api/simulation/pause")
def pause() -> dict[str, Any]:
    service.world.running = False
    return state()


@app.post("/api/simulation/reset")
def reset() -> dict[str, Any]:
    service.reset()
    return state()


@app.post("/api/operator")
def operator(request: OperatorCommand) -> dict[str, Any]:
    response = service.ask(request)
    return {
        **response.model_dump(mode="json"),
        "state": state() if response.state_changed else None,
    }


@app.get("/api/copilotkit/actions")
def copilotkit_actions() -> dict[str, Any]:
    return {
        "actions": [
            {"name": "simulation_control", "values": ["play", "pause", "step", "reset"]},
            {"name": "generate_scenario", "parameters": ["seed"]},
            {"name": "inspect_agent", "parameters": ["agent_id"]},
            {"name": "query_operations", "parameters": ["command"]},
        ]
    }


@app.get("/api/snowflake/entities/{seed}")
def snowflake_entities(seed: int) -> dict[str, Any]:
    """Return survivor and blockage GeoJSON from the raw Snowflake tables."""
    layers = _snowflake_layers(seed)
    if not layers:
        raise HTTPException(status_code=503, detail="Snowflake layers are unavailable")
    return layers


frontend = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if frontend.exists():
    app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend_app(path: str) -> FileResponse:
        candidate = frontend / path
        return FileResponse(candidate if candidate.is_file() else frontend / "index.html")
