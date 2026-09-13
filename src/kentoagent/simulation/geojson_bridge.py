"""Convert MapEntity lists into GeoJSON FeatureCollections for MapLibre."""

from __future__ import annotations

from typing import Any

from kentoagent.storage.scenario_provider import MapEntity


def entities_to_geojson(entities: list[MapEntity]) -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection from a list of map entities.

    Coordinates use ``[longitude, latitude]`` order per the GeoJSON spec.
    """
    features: list[dict[str, Any]] = []
    for e in entities:
        props: dict[str, Any] = {
            "entity_type": e.entity_type,
            "seed": e.seed,
            "severity": e.severity,
        }
        if e.visible is not None:
            props["visible"] = e.visible
        if e.trapped is not None:
            props["trapped"] = e.trapped
        if e.status is not None:
            props["status"] = e.status
        props.update(e.properties)

        features.append(
            {
                "type": "Feature",
                "id": e.entity_id,
                "properties": props,
                "geometry": {
                    "type": "Point",
                    "coordinates": [e.longitude, e.latitude],
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


def entities_to_layer_geojson(
    entities: list[MapEntity],
) -> dict[str, dict[str, Any]]:
    """Split entities into per-type FeatureCollections matching MapLibre layers.

    Returns a dict keyed by entity type with GeoJSON FeatureCollection values,
    compatible with the existing ``MapView`` layer IDs.
    """
    grouped: dict[str, list[MapEntity]] = {}
    for e in entities:
        grouped.setdefault(e.entity_type, []).append(e)

    return {
        entity_type: entities_to_geojson(group)
        for entity_type, group in grouped.items()
    }
