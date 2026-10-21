"""No-model smoke checks against the running loopback BFF; never print session tokens."""
import json
from pathlib import Path
from datetime import datetime, timezone
import httpx


def main():
    root = Path(__file__).resolve().parents[2]
    with httpx.Client(base_url="http://127.0.0.1:8895", trust_env=False, timeout=15) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/production-capabilities").status_code == 401
        assert client.get("/api/v1/production-jobs").status_code == 401
        assert client.get("/api/v1/production-jobs/nonexistent-smoke-check/playback").status_code == 401
        login = client.post("/api/v1/auth/local")
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["data"]["accessToken"]
        try:
            response = client.get("/api/v1/production-capabilities")
            response.raise_for_status()
            caps = response.json()["data"]
            listing = client.get("/api/v1/production-jobs", params={"limit": 1})
            listing.raise_for_status()
            assert isinstance(listing.json()["data"]["items"], list)
            assert "next_offset" in listing.json()["data"]
            assert client.get("/api/v1/production-jobs", params={"limit": 101}).status_code == 422
            assert all(caps["modules"][name]["configured"] for name in ("story", "audio", "visual_plan", "visual_render", "integrate"))
            assert client.post("/api/v1/production-jobs", json={"invalid": True}).status_code == 422
            assert client.get("/api/v1/production-jobs/nonexistent-smoke-check").status_code == 404
            assert client.get("/api/v1/production-jobs/nonexistent-smoke-check/playback").status_code == 404
            report = {"checked_at": datetime.now(timezone.utc).isoformat(), "api": "passed", "authentication_required": True,
                "modules_configured": caps["modules"], "invalid_submission_rejected": True, "job_listing": "passed",
                "playback_auth_and_missing_job": "passed", "new_model_calls": 0,
                "real_model_acceptance": False, "public_deployment": False}
            target = root / "ops/my-legend/evidence/production-service-20261019.json"
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
        finally:
            client.post("/api/v1/auth/logout")


if __name__ == "__main__":
    main()
