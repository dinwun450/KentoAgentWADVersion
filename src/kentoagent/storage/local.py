from __future__ import annotations

from pathlib import Path

from kentoagent.simulation.entities import WorldState


class JsonSimulationStore:
    def __init__(self, root: Path | str = ".kentoagent/runs") -> None:
        self.root = Path(root)

    def save(self, state: WorldState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{state.run_id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)

    def load(self, run_id: str) -> WorldState:
        return WorldState.model_validate_json(
            (self.root / f"{run_id}.json").read_text(encoding="utf-8")
        )

    def list_runs(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(path.stem for path in self.root.glob("run-*.json"))
