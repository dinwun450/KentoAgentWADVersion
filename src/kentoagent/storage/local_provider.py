"""Local scenario provider — wraps the existing deterministic generator."""

from __future__ import annotations

from kentoagent.simulation.entities import Coordinate
from kentoagent.simulation.scenario import ScenarioConfig, generate_scenario
from kentoagent.storage.scenario_provider import MapEntity
from kentoagent.storage.snowflake_loader import (
    GENERATION_VERSION,
    extract_blockage_rows,
    extract_survivor_rows,
)


class LocalScenarioProvider:
    """Generate map entities locally without any Snowflake dependency."""

    def get_entities(self, seed: int) -> list[MapEntity]:
        config = ScenarioConfig(seed=seed)
        world = generate_scenario(config)

        entities: list[MapEntity] = []

        for row in extract_blockage_rows(world):
            entities.append(
                MapEntity(
                    entity_id=row["blockage_id"],
                    entity_type="blockage",
                    seed=row["seed"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    severity=row["severity"],
                    generation_version=row["generation_version"],
                    properties={
                        "active": row["active"],
                        "road_segment_id": row["road_segment_id"],
                        "blockage_type": row["blockage_type"],
                    },
                )
            )

        for row in extract_survivor_rows(world):
            entity_type = (
                "trapped_survivor" if row["trapped"] else "visible_injured_survivor"
            )
            entities.append(
                MapEntity(
                    entity_id=row["survivor_id"],
                    entity_type=entity_type,
                    seed=row["seed"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    severity=row["injury_severity"],
                    visible=row["visible"],
                    trapped=row["trapped"],
                    status=row["status"],
                    generation_version=row["generation_version"],
                )
            )

        return entities
