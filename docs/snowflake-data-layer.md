# Snowflake + dbt Data Layer

Deterministic seeded spatial scenario data for KentoAgent's MapLibre map.

## Architecture

```
Seed (e.g. 42)
  ↓  generate_scenario() — deterministic Python RNG
Snowflake RAW_KENTOAGENT tables (scenarios, blockages, survivors)
  ↓  dbt run
dbt mart: map_entities (unified view)
  ↓  ScenarioProvider.get_entities(seed)
list[MapEntity]
  ↓  entities_to_geojson()
GeoJSON FeatureCollection
  ↓  source.setData()
MapLibre map layers
```

## Setup

### 1. Environment variables

Copy `.env.example` to `.env` and fill in Snowflake credentials:

```bash
SNOWFLAKE_ACCOUNT=QINZYEH-EPB26772
SNOWFLAKE_USER=SFDATACAMPER
SNOWFLAKE_PASSWORD=<your-password>
SNOWFLAKE_DATABASE=KENTOAGENT
SNOWFLAKE_SCHEMA=RAW_KENTOAGENT
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_ROLE=SYSADMIN
KENTO_SCENARIO_PROVIDER=local   # or "snowflake"
```

### 2. Create Snowflake objects

Run the DDL in a Snowflake worksheet:

```sql
-- snowflake/ddl/raw_kentoagent.sql
```

This creates: `KENTOAGENT.RAW_KENTOAGENT.scenarios`, `.blockages`, `.survivors`.

### 3. Load seed data

```bash
# Install with Snowflake support
pip install -e ".[snowflake]"

# Load seed 42 into Snowflake
python -m kentoagent.storage.snowflake_loader 42
```

### 4. Run dbt

```bash
cd dbt_project
pip install dbt-snowflake
dbt deps
dbt run
dbt test
```

## Generating seed 42

### Method A: Local (no Snowflake)

```python
from kentoagent.storage.local_provider import LocalScenarioProvider

provider = LocalScenarioProvider()
entities = provider.get_entities(42)
# Returns list[MapEntity] with blockages + survivors
```

### Method B: Snowflake

```python
# After loading and running dbt:
from kentoagent.storage.snowflake_provider import SnowflakeScenarioProvider

provider = SnowflakeScenarioProvider()
entities = provider.get_entities(42)
```

### Method C: Factory (reads KENTO_SCENARIO_PROVIDER env var)

```python
from kentoagent.storage.scenario_provider import create_scenario_provider

provider = create_scenario_provider()
entities = provider.get_entities(42)
```

## Querying seed 42 in Snowflake

```sql
-- All entities for seed 42
SELECT * FROM DBT_KENTOAGENT.map_entities WHERE seed = 42;

-- Only blockages
SELECT * FROM DBT_KENTOAGENT.map_blockages WHERE seed = 42;

-- Only visible injured survivors
SELECT * FROM DBT_KENTOAGENT.map_visible_injured_survivors WHERE seed = 42;

-- Only trapped survivors
SELECT * FROM DBT_KENTOAGENT.map_trapped_survivors WHERE seed = 42;
```

## Converting to GeoJSON

```python
from kentoagent.simulation.geojson_bridge import entities_to_geojson

geojson = entities_to_geojson(entities)
# {
#   "type": "FeatureCollection",
#   "features": [
#     {
#       "type": "Feature",
#       "id": "blockage_42_0001",
#       "properties": {
#         "entity_type": "blockage",
#         "seed": 42,
#         "severity": "major"
#       },
#       "geometry": {
#         "type": "Point",
#         "coordinates": [-122.414, 37.774]  // [lon, lat]
#       }
#     },
#     ...
#   ]
# }
```

## Regeneration

Re-running with the same seed always produces identical rows:

```python
from kentoagent.storage.snowflake_loader import generate_and_extract, ScenarioLoadConfig

cfg = ScenarioLoadConfig(seed=42)
_, blockages_a, survivors_a = generate_and_extract(cfg)
_, blockages_b, survivors_b = generate_and_extract(cfg)
assert blockages_a == blockages_b
assert survivors_a == survivors_b
```

Changing `generation_version` in the loader code marks a new generation epoch.
The identity tuple `(seed, generation_version, map boundary)` is the scenario key.

## Configuration

The `ScenarioLoadConfig` accepts:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `seed` | 42 | Scenario seed |
| `survivor_count` | 8 | Number of survivors |
| `agent_count` | 5 | Number of agents |
| `hazard_density` | 0.18 | Fraction of nodes with hazards |
| `blocked_road_probability` | 0.16 | Fraction of roads blocked |
| `region_name` | SF SoMa | Map region label |
| `center_lon` / `center_lat` | -122.414 / 37.774 | Grid center |

## Provider architecture

```
ScenarioProvider (Protocol)
├── LocalScenarioProvider   — uses generate_scenario() directly
└── SnowflakeScenarioProvider — queries dbt map_entities table
```

Selected via `KENTO_SCENARIO_PROVIDER=local|snowflake` or by calling
`create_scenario_provider("local")` / `create_scenario_provider("snowflake")`.

## Entity types

| Type | trapped | visible | Description |
|------|---------|---------|-------------|
| `blockage` | null | null | Blocked road midpoint |
| `visible_injured_survivor` | false | true | Detected, not trapped |
| `trapped_survivor` | true | varies | Under rubble |

## Deterministic generation strategy

Uses Python `random.Random(seed)` via the existing `generate_scenario()` function.
The same seed + configuration always produces identical `WorldState`, from which
blockage and survivor rows are extracted with deterministic IDs:

- `blockage_{seed}_{seq:04d}` (sequence from sorted blocked road IDs)
- `survivor_{seed}_{seq:04d}` (sequence from sorted survivor IDs)

No SQL-level randomness is used. Snowflake stores pre-computed results.

## Verification checklist

- [ ] Seed 42 generates blockages, visible injured survivors, and trapped survivors
- [ ] All coordinates fall within the SF SoMa bounding box (~37.764–37.784, ~-122.420–-122.408)
- [ ] Re-running seed 42 gives identical rows
- [ ] Seed 43 produces different rows
- [ ] dbt tests pass (`dbt test`)
- [ ] GeoJSON output uses `[longitude, latitude]` coordinate order
- [ ] The GeoJSON can be consumed by MapLibre's `source.setData()`
