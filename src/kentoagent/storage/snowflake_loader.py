"""Load deterministically-generated scenario data into Snowflake raw tables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from kentoagent.simulation.entities import Coordinate, WorldState
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario

GENERATION_VERSION = "v1"


@dataclass
class ScenarioLoadConfig:
    seed: int = 42
    survivor_count: int = 8
    agent_count: int = 5
    hazard_density: float = 0.18
    blocked_road_probability: float = 0.16
    region_name: str = "San Francisco — SoMa / Mission"
    center_lon: float = -122.414
    center_lat: float = 37.774
    blockage_count_override: int | None = None
    visible_survivor_count_override: int | None = None
    trapped_survivor_count_override: int | None = None


def _midpoint(a: Coordinate, b: Coordinate) -> tuple[float, float]:
    return ((a.lat + b.lat) / 2, (a.lon + b.lon) / 2)


def _bounding_box(world: WorldState) -> tuple[float, float, float, float]:
    lats = [n.position.lat for n in world.nodes.values()]
    lons = [n.position.lon for n in world.nodes.values()]
    return (min(lats), min(lons), max(lats), max(lons))


def extract_blockage_rows(world: WorldState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    blocked = sorted(
        [r for r in world.roads.values() if r.blocked], key=lambda r: r.id
    )
    for seq, road in enumerate(blocked, start=1):
        coords = road.coordinates
        lat, lon = _midpoint(coords[0], coords[-1])
        rows.append(
            {
                "blockage_id": f"blockage_{world.seed}_{seq:04d}",
                "seed": world.seed,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "severity": "major" if road.hazard_cost > 100 else "moderate",
                "active": True,
                "road_segment_id": road.id,
                "blockage_type": "road_block",
                "generation_version": GENERATION_VERSION,
                "metadata": json.dumps({"hazard_cost": road.hazard_cost}),
            }
        )
    return rows


def extract_survivor_rows(world: WorldState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    survivors = sorted(world.survivors.values(), key=lambda s: s.id)
    for seq, s in enumerate(survivors, start=1):
        rows.append(
            {
                "survivor_id": f"survivor_{world.seed}_{seq:04d}",
                "seed": world.seed,
                "latitude": round(s.position.lat, 6),
                "longitude": round(s.position.lon, 6),
                "injury_severity": s.severity.value,
                "status": "waiting",
                "visible": not s.trapped,
                "trapped": s.trapped,
                "generation_version": GENERATION_VERSION,
            }
        )
    return rows


def extract_scenario_row(
    world: WorldState, config: ScenarioLoadConfig
) -> dict[str, Any]:
    min_lat, min_lon, max_lat, max_lon = _bounding_box(world)
    return {
        "seed": world.seed,
        "generation_version": GENERATION_VERSION,
        "area_id": world.region_name,
        "min_latitude": round(min_lat, 6),
        "min_longitude": round(min_lon, 6),
        "max_latitude": round(max_lat, 6),
        "max_longitude": round(max_lon, 6),
        "configuration": json.dumps(
            {
                "survivor_count": config.survivor_count,
                "hazard_density": config.hazard_density,
                "blocked_road_probability": config.blocked_road_probability,
            }
        ),
    }


def generate_and_extract(
    config: ScenarioLoadConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    scenario_config = ScenarioConfig(
        seed=config.seed,
        survivor_count=config.survivor_count,
        agent_count=config.agent_count,
        hazard_density=config.hazard_density,
        blocked_road_probability=config.blocked_road_probability,
        region_name=config.region_name,
        center=Coordinate(lon=config.center_lon, lat=config.center_lat),
    )
    world = generate_scenario(scenario_config)
    scenario_row = extract_scenario_row(world, config)
    blockage_rows = extract_blockage_rows(world)
    survivor_rows = extract_survivor_rows(world)
    return scenario_row, blockage_rows, survivor_rows


def _get_connection():
    """Create a Snowflake connection from environment variables."""
    from dotenv import load_dotenv
    import snowflake.connector

    load_dotenv()
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        database=os.environ.get("SNOWFLAKE_DATABASE", "KENTOAGENT"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "RAW_KENTOAGENT"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        role=os.environ.get("SNOWFLAKE_ROLE") or None,
    )


def load_to_snowflake(config: ScenarioLoadConfig | None = None) -> dict[str, int]:
    if config is None:
        config = ScenarioLoadConfig()

    scenario_row, blockage_rows, survivor_rows = generate_and_extract(config)
    conn = _get_connection()
    try:
        cur = conn.cursor()
        seed = config.seed
        ver = GENERATION_VERSION

        # Idempotent: delete existing data for this seed+version, then insert
        cur.execute(
            "DELETE FROM scenarios WHERE seed = %s AND generation_version = %s",
            (seed, ver),
        )
        cur.execute(
            "DELETE FROM blockages WHERE seed = %s AND generation_version = %s",
            (seed, ver),
        )
        cur.execute(
            "DELETE FROM survivors WHERE seed = %s AND generation_version = %s",
            (seed, ver),
        )

        # Insert scenario
        cur.execute(
            """INSERT INTO scenarios
               (seed, generation_version, area_id,
                min_latitude, min_longitude, max_latitude, max_longitude,
                configuration)
               SELECT %s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s)""",
            (
                scenario_row["seed"],
                scenario_row["generation_version"],
                scenario_row["area_id"],
                scenario_row["min_latitude"],
                scenario_row["min_longitude"],
                scenario_row["max_latitude"],
                scenario_row["max_longitude"],
                scenario_row["configuration"],
            ),
        )

        # Insert blockages
        for row in blockage_rows:
            cur.execute(
                """INSERT INTO blockages
                   (blockage_id, seed, latitude, longitude, severity, active,
                    road_segment_id, blockage_type, generation_version, metadata)
                   SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s)""",
                (
                    row["blockage_id"],
                    row["seed"],
                    row["latitude"],
                    row["longitude"],
                    row["severity"],
                    row["active"],
                    row["road_segment_id"],
                    row["blockage_type"],
                    row["generation_version"],
                    row["metadata"],
                ),
            )

        # Insert survivors
        for row in survivor_rows:
            cur.execute(
                """INSERT INTO survivors
                   (survivor_id, seed, latitude, longitude, injury_severity,
                    status, visible, trapped, generation_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    row["survivor_id"],
                    row["seed"],
                    row["latitude"],
                    row["longitude"],
                    row["injury_severity"],
                    row["status"],
                    row["visible"],
                    row["trapped"],
                    row["generation_version"],
                ),
            )

        conn.commit()
        return {
            "blockages": len(blockage_rows),
            "survivors": len(survivor_rows),
            "seed": seed,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 42
    cfg = ScenarioLoadConfig(seed=seed)
    result = load_to_snowflake(cfg)
    print(f"Loaded seed {result['seed']}: "
          f"{result['blockages']} blockages, {result['survivors']} survivors")
