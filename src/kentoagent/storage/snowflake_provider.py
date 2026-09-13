"""Snowflake-backed scenario provider — queries the dbt mart for map entities."""

from __future__ import annotations

import os
from dotenv import load_dotenv

from kentoagent.storage.scenario_provider import MapEntity

load_dotenv()  # Load environment variables from .env file


class SnowflakeScenarioProvider:
    """Retrieve map entities from the Snowflake ``map_entities`` mart."""

    def __init__(self) -> None:
        self._conn_params = {
            "account": os.getenv("SNOWFLAKE_ACCOUNT"),
            "user": os.getenv("SNOWFLAKE_USER"),
            "password": os.getenv("SNOWFLAKE_PASSWORD"),
            "database": os.getenv("SNOWFLAKE_DATABASE", "KENTOAGENT"),
            "schema": os.getenv("SNOWFLAKE_SCHEMA", "DBT_KENTOAGENT"),
            "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
            "role": os.getenv("SNOWFLAKE_ROLE") or None,
        }
        self._raw_schema = os.getenv("SNOWFLAKE_RAW_SCHEMA", "RAW_KENTOAGENT")

    def _connect(self, schema_override: str | None = None):
        import snowflake.connector

        params = self._conn_params
        if schema_override:
            params = {**params, "schema": schema_override}
        return snowflake.connector.connect(**params)

    def get_entities(self, seed: int) -> list[MapEntity]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT entity_id, entity_type, seed, latitude, longitude,
                       severity, visible, trapped, status, generation_version
                FROM map_entities
                WHERE seed = %s
                ORDER BY entity_type, entity_id
                """,
                (seed,),
            )
            columns = [desc[0].lower() for desc in cur.description]
            entities: list[MapEntity] = []
            for row in cur.fetchall():
                data = dict(zip(columns, row))
                entities.append(
                    MapEntity(
                        entity_id=data["entity_id"],
                        entity_type=data["entity_type"],
                        seed=data["seed"],
                        latitude=data["latitude"],
                        longitude=data["longitude"],
                        severity=data["severity"] or "unknown",
                        visible=data.get("visible"),
                        trapped=data.get("trapped"),
                        status=data.get("status"),
                        generation_version=data.get("generation_version", "v1"),
                    )
                )
            return entities
        finally:
            conn.close()

    def get_raw_entities(self, seed: int) -> list[MapEntity]:
        """Query the raw survivors and blockages tables directly."""
        db = self._conn_params["database"]
        raw = self._raw_schema
        conn = self._connect(schema_override=raw)
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT survivor_id AS entity_id, 'survivor' AS entity_type,
                       seed, latitude, longitude, injury_severity AS severity,
                       visible, trapped, status, generation_version
                FROM {db}.{raw}.survivors
                WHERE seed = %s
                UNION ALL
                SELECT blockage_id AS entity_id, 'blockage' AS entity_type,
                       seed, latitude, longitude, severity,
                       NULL AS visible, NULL AS trapped, NULL AS status,
                       generation_version
                FROM {db}.{raw}.blockages
                WHERE seed = %s AND active = TRUE
                ORDER BY entity_type, entity_id
                """,
                (seed, seed),
            )
            columns = [desc[0].lower() for desc in cur.description]
            entities: list[MapEntity] = []
            for row in cur.fetchall():
                data = dict(zip(columns, row))
                entities.append(
                    MapEntity(
                        entity_id=data["entity_id"],
                        entity_type=data["entity_type"],
                        seed=data["seed"],
                        latitude=data["latitude"],
                        longitude=data["longitude"],
                        severity=data["severity"] or "unknown",
                        visible=data.get("visible"),
                        trapped=data.get("trapped"),
                        status=data.get("status"),
                        generation_version=data.get("generation_version", "v1"),
                    )
                )
            return entities
        finally:
            conn.close()
