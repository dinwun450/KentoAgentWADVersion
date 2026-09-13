"""ScenarioProvider protocol and MapEntity data class."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class MapEntity(BaseModel):
    """A single map-renderable entity (blockage or survivor)."""

    entity_id: str
    entity_type: str
    seed: int
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    severity: str
    visible: bool | None = None
    trapped: bool | None = None
    status: str | None = None
    generation_version: str = "v1"
    properties: dict[str, Any] = Field(default_factory=dict)


class ScenarioProvider(Protocol):
    """Retrieve map entities for a given seed."""

    def get_entities(self, seed: int) -> list[MapEntity]: ...


def create_scenario_provider(provider_type: str | None = None) -> ScenarioProvider:
    """Factory that returns the configured provider.

    Uses the ``KENTO_SCENARIO_PROVIDER`` env var when *provider_type* is not
    given explicitly.  Falls back to ``"local"`` when unset.
    """
    import os

    kind = (provider_type or os.environ.get("KENTO_SCENARIO_PROVIDER", "local")).lower()
    if kind == "snowflake":
        from kentoagent.storage.snowflake_provider import SnowflakeScenarioProvider

        return SnowflakeScenarioProvider()
    from kentoagent.storage.local_provider import LocalScenarioProvider

    return LocalScenarioProvider()
