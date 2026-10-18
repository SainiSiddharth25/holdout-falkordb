"""HTTP integration exercises the real service, not a fabricated state fixture."""
from fastapi.testclient import TestClient

from holdout.api import create_app
from holdout.service import HoldoutService


def test_api_investigation_export_and_origin(tmp_path):
    service = HoldoutService(runtime_dir=tmp_path / "api")
    try:
        with TestClient(create_app(service=service)) as client:
            initial = client.get("/api/state")
            assert initial.status_code == 200
            assert initial.json()["engine"]["native_maxflow"] is True
            assert client.get("/").status_code == 200
            assert client.get("/api/candidates/c401").json()["train"]["content"]
            blocked = client.post("/api/investigate", json={"candidate_id": "c401"}, headers={"Origin": "https://unrelated.example"})
            assert blocked.status_code == 403
            changed = client.post("/api/investigate", json={"candidate_id": "c401"})
            assert changed.status_code == 200
            assert next(c for c in changed.json()["candidates"] if c["id"] == "c401")["status"] == "confirmed"
            stale = client.post("/api/release", json={"expected_revision": initial.json()["revision"]})
            assert stale.status_code == 409
            assert client.post("/api/release", json={"conservative": False}).status_code == 400
            released = client.post("/api/release", json={"conservative": True})
            assert released.status_code == 200 and released.json()["release"]["verified"]
            assert client.get("/api/artifacts/train.jsonl").status_code == 200
            assert client.get("/api/artifacts/unknown").status_code == 404
            assert client.post("/api/investigate", json={"candidate_id": "c999"}).status_code == 404
            assert client.post("/api/run", json={"mode": "made-up"}).status_code == 400
    finally:
        service.close()
