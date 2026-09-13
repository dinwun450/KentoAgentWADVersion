import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { CopilotBridge } from "./CopilotBridge";
import { MapView } from "./MapView";
import { formatSimulationTime } from "./time";
import type { Agent, Snapshot } from "./types";

type DurableRunStatus = {
  phase: string;
  active_roles: string[];
  state: Snapshot | null;
};

async function api(path: string, body?: unknown): Promise<Snapshot> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`API ${response.status}`);
  return response.json();
}

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [seed, setSeed] = useState(49281);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [durableId, setDurableId] = useState<string | null>(null);
  const [durablePhase, setDurablePhase] = useState<string | null>(null);
  const [durableRoles, setDurableRoles] = useState<string[]>([]);
  const [question, setQuestion] = useState("Which survivor is currently highest priority?");
  const [answer, setAnswer] = useState("Operator assistant ready.");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSnapshot(await api("/api/state"));
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to reach simulator");
    }
  }, []);

  useEffect(() => void refresh(), [refresh]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(async () => {
      const next = await api("/api/simulation/step", {});
      setSnapshot(next);
      if (next.world.stopped_reason) setPlaying(false);
    }, 1000 / speed);
    return () => window.clearInterval(timer);
  }, [playing, speed]);

  useEffect(() => {
    if (!durableId || durablePhase === "completed") return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch(`/api/durable/runs/${durableId}`);
        if (!response.ok) throw new Error(`Durable status API ${response.status}`);
        const status = await response.json() as DurableRunStatus;
        if (cancelled) return;
        setDurablePhase(status.phase);
        setDurableRoles(status.active_roles);
        if (status.state) setSnapshot(status.state);
        setError(null);
        if (status.phase !== "completed") timer = window.setTimeout(poll, 1000);
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Durable workflow unavailable");
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [durableId, durablePhase]);

  const selected: Agent | null = useMemo(
    () => (snapshot && selectedId ? snapshot.world.agents[selectedId] : null),
    [snapshot, selectedId],
  );

  async function control(action: "play" | "pause" | "step" | "reset") {
    if (action === "play") setPlaying(true);
    if (action === "pause") setPlaying(false);
    setSnapshot(await api(`/api/simulation/${action}`, {}));
  }

  async function generate() {
    setPlaying(false);
    setSnapshot(await api("/api/scenarios", { seed }));
    setSelectedId(null);
  }

  async function startDurable() {
    try {
      setPlaying(false);
      const response = await fetch("/api/durable/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          seed,
          agent_count: 5,
          provider: "openai",
          step_interval_s: 1,
        }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail ?? `Durable start API ${response.status}`);
      }
      const run = await response.json();
      setDurableId(run.workflow_id);
      setDurablePhase("starting");
      setDurableRoles([]);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to start durable workflow");
    }
  }

  async function controlDurable(command: "pause" | "resume" | "stop") {
    if (!durableId) return;
    try {
      const response = await fetch(`/api/durable/runs/${durableId}/${command}`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(`Durable control API ${response.status}`);
      setDurablePhase(command === "pause" ? "paused" : command === "stop" ? "stopping" : "running");
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Durable control failed");
    }
  }

  async function loadFromSnowflake() {
    try {
      const response = await fetch(`/api/snowflake/entities/${seed}`);
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(detail?.detail ?? `API ${response.status}`);
      }
      const layers = await response.json();
      setSnapshot((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          geojson: { ...prev.geojson, ...layers },
        };
      });
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load Snowflake data");
    }
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    const response = await fetch("/api/operator", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: question, agent_id: selectedId }),
    });
    const payload = await response.json();
    setAnswer(payload.answer);
    if (payload.selected_agent_id) setSelectedId(payload.selected_agent_id);
    if (payload.state) setSnapshot(payload.state);
  }

  if (!snapshot) return <main className="loading">Connecting to incident command… {error}</main>;
  const agents = Object.values(snapshot.world.agents);

  return (
    <main className="shell">
      <header className="topbar">
        <div><span className="eyebrow">AUTONOMOUS HIVE / INCIDENT 07</span><h1>KentoAgent</h1></div>
        <div className="clock"><span>SIMULATION TIME</span><strong>{formatSimulationTime(snapshot.world.simulation_time_s)}</strong></div>
        <div className="run-meta"><span>SEED {snapshot.world.seed}</span><span>STEP {snapshot.world.step}</span><span className={playing ? "live" : "paused"}>{playing ? "RUNNING" : "PAUSED"}</span></div>
      </header>

      <section className="workspace">
        <aside className="left-panel panel">
          <div className="panel-title">MISSION CONTROL</div>
          <div className="controls">
            <button className="primary" onClick={() => void control("play")}>▶ Play</button>
            <button onClick={() => void control("pause")}>Ⅱ Pause</button>
            <button onClick={() => void control("step")}>Step →</button>
            <button onClick={() => void control("reset")}>Reset</button>
          </div>
          <label>Scenario seed<input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></label>
          <button className="generate" onClick={() => void generate()}>Generate scenario</button>
          <button className="generate" onClick={() => void loadFromSnowflake()}>Load from Snowflake</button>
          <button className="generate" onClick={() => void startDurable()}>Start five-LLM workflow</button>
          {durableId && <div className="controls">
            <button onClick={() => void controlDurable("pause")}>Pause LLMs</button>
            <button onClick={() => void controlDurable("resume")}>Resume LLMs</button>
            <button onClick={() => void controlDurable("stop")}>Stop LLMs</button>
          </div>}
          {durablePhase && <p className="simulation-note">
            TEMPORAL · {durablePhase.toUpperCase()}
            {durableRoles.length > 0 ? ` · ${durableRoles.join(" + ").toUpperCase()}` : ""}
          </p>}
          <label>Simulation speed <b>{speed}×</b><input type="range" min="1" max="5" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} /></label>
          <div className="metrics-grid">
            <Metric label="Rescue rate" value={`${Math.round(snapshot.metrics.rescue_rate * 100)}%`} />
            <Metric label="Critical" value={`${Math.round(snapshot.metrics.critical_survivor_rescue_rate * 100)}%`} />
            <Metric label="Replans" value={snapshot.metrics.replans} />
            <Metric label="Unresolved" value={snapshot.metrics.unresolved_survivors} />
          </div>
          <div className="legend"><span className="agent-dot" /> Agent <span className="survivor-dot" /> Visible injured <span className="buried-dot" /> Buried <span className="hazard-dot" /> Hazard <span className="blocked-line" /> Blocked <span className="blockage-dot" /> Blockage</div>
          <p className="simulation-note">SIMULATION OVER A REAL-WORLD BASEMAP — NOT LIVE DISASTER DATA</p>
        </aside>

        <section className="map-wrap">
          <MapView snapshot={snapshot} onSelectAgent={setSelectedId} />
          <div className="location-chip">{snapshot.world.region_name}</div>
          <div className="agent-count-chip">AGENTS · {agents.length}</div>
          {snapshot.world.stopped_reason && <div className="stop-chip">STOPPED · {snapshot.world.stopped_reason}</div>}
        </section>

        <aside className="right-panel panel">
          <div className="panel-title">AGENT MANAGEMENT <span>{agents.length} ACTIVE</span></div>
          <div className="agent-list">
            {agents.map((agent) => (
              <button key={agent.id} className={`agent-card ${selectedId === agent.id ? "selected" : ""}`} onClick={() => setSelectedId(agent.id)}>
                <span className={`role-mark ${agent.role}`} />
                <span><b>{agent.name}</b><small>{agent.role.toUpperCase()} · {agent.status}</small></span>
                <em>{agent.current_assignment ?? "STANDBY"}</em>
              </button>
            ))}
          </div>
          <DecisionPanel agent={selected} />
          <form className="operator" onSubmit={(event) => void ask(event)}>
            <div className="panel-title">OPERATOR COPILOT</div>
            <p>{answer}</p>
            <input value={question} onChange={(e) => setQuestion(e.target.value)} aria-label="Ask KentoAgent" />
            <button type="submit">Ask system</button>
            <small>Offline command mode. Set VITE_COPILOT_RUNTIME_URL for CopilotKit v2.</small>
          </form>
        </aside>
      </section>
      <CopilotBridge snapshot={snapshot} refresh={refresh} selectAgent={setSelectedId} />
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="metric"><strong>{value}</strong><span>{label}</span></div>;
}

function DecisionPanel({ agent }: { agent: Agent | null }) {
  if (!agent) return <section className="decision empty">Select an agent on the map or roster.</section>;
  const detail = agent.decision;
  return <section className="decision">
    <div className="panel-title">DECISION / {agent.id}</div>
    <DecisionRow label="OBJECTIVE" value={detail?.objective ?? agent.objective} />
    <DecisionRow label="OBSERVATIONS" value={detail?.observations.join(" · ") ?? "No decision yet"} />
    <DecisionRow label="CONSTRAINTS" value={detail?.constraints.join(" · ") ?? "World-state guardrails"} />
    <DecisionRow label="ACTION" value={detail?.action ?? agent.last_action} />
    <DecisionRow label="VALIDATION" value={detail?.validation ?? "Pending"} />
    <DecisionRow label="RESULT" value={detail?.result ?? agent.status} />
    <DecisionRow label="NEXT STEP" value={detail?.next_step ?? "Await orchestration"} />
  </section>;
}

function DecisionRow({ label, value }: { label: string; value: string }) {
  return <div className="decision-row"><span>{label}</span><p>{value}</p></div>;
}
