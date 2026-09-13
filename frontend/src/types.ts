import type { FeatureCollection as GeoJSONFeatureCollection, Geometry } from "geojson";

export type Coordinate = { lon: number; lat: number };

export type Decision = {
  objective: string;
  observations: string[];
  constraints: string[];
  action: string;
  validation: string;
  result: string;
  next_step: string;
};

export type Agent = {
  id: string;
  name: string;
  role: string;
  status: string;
  position: Coordinate;
  current_assignment: string | null;
  route: string[];
  objective: string;
  last_action: string;
  confidence: number | null;
  recent_messages: string[];
  decision: Decision | null;
};

export type World = {
  run_id: string;
  scenario_id: string;
  seed: number;
  region_name: string;
  center: Coordinate;
  agents: Record<string, Agent>;
  survivors: Record<string, { id: string; severity: string; status: string; trapped: boolean }>;
  step: number;
  simulation_time_s: number;
  running: boolean;
  stopped_reason: string | null;
};

export type FeatureCollection = GeoJSONFeatureCollection<Geometry>;

export type Snapshot = {
  world: World;
  geojson: Record<string, FeatureCollection>;
  metrics: {
    rescue_rate: number;
    critical_survivor_rescue_rate: number;
    invalid_actions: number;
    replans: number;
    unresolved_survivors: number;
  };
};
