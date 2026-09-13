from fastapi.testclient import TestClient

from kentoagent.api.app import app


def test_api_generates_and_steps_scenario() -> None:
    client = TestClient(app)
    generated = client.post("/api/scenarios", json={"seed": 99}).json()
    assert generated["world"]["seed"] == 99
    stepped = client.post("/api/simulation/step").json()
    assert stepped["world"]["step"] == 1
    assert "agent-layer" in stepped["geojson"]


def test_operator_queries_structured_state() -> None:
    client = TestClient(app)
    response = client.post("/api/operator", json={"command": "Which agents are idle?"})
    assert response.status_code == 200
    assert response.json()["answer"].startswith("Idle agents:")
