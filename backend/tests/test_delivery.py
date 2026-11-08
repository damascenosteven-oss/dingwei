from datetime import datetime, timedelta, timezone

from test_end_to_end import enroll_elder


def test_receipts_and_capture_order(client, admin):
    elder, token = enroll_elder(client, admin, "模拟老人", "qa-android-test")
    headers = {"Authorization": f"Bearer {token}"}
    now = datetime.now(timezone.utc)
    base = f"/api/v1/families/{admin['family_id']}/elders/{elder}"

    def upload(seq, captured, **extra):
        point = dict(
            sequence=seq,
            capturedAt=captured.isoformat(),
            latitude=30,
            longitude=120,
            accuracyM=8,
            batteryPct=80,
            coordinateSystem="GCJ02",
            **extra,
        )
        result = client.post(
            "/api/v1/device/locations:batch", headers=headers, json={"points": [point]}
        )
        assert result.status_code == 200, result.text
        return result.json()

    first = upload(1, now - timedelta(minutes=12))
    assert first["lastAcceptedAt"]
    assert upload(2, now - timedelta(minutes=30))["acceptedSequences"] == [2]
    latest = client.get(base + "/latest").json()
    assert latest["sequence"] == 1
    assert latest["stale"] and latest["coordinateSystem"] == "GCJ02"
    assert datetime.fromisoformat(latest["capturedAt"]) == now - timedelta(minutes=12)
    assert client.get(base + "/device-status").json()["state"] == "stale"
    assert upload(1, now - timedelta(minutes=12))["lastAcceptedAt"] is None
    rejected = upload(3, now + timedelta(days=1))
    assert rejected["lastAcceptedAt"] is None and rejected["rejected"][0]["sequence"] == 3
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import EventLog

    with SessionLocal() as db:
        event = db.scalar(select(EventLog).where(EventLog.event_type == "location.rejected"))
        assert event is not None
        assert event.payload["sequences"] == [3]
    assert upload(4, now, isMock=True)["acceptedSequences"] == [4]
    assert client.get(base + "/latest").json() == latest


def test_stop_cancels_command_and_rejects_old_device(client, admin):
    elder, token = enroll_elder(client, admin, "模拟老人", "qa-stop-device")
    base = f"/api/v1/families/{admin['family_id']}/elders/{elder}"
    headers = {"Authorization": f"Bearer {token}"}
    req = client.post(base + "/locate-requests", headers=admin["headers"]).json()
    assert client.post("/api/v1/device/stop-sharing", headers=headers).status_code == 204
    assert client.get(base + "/device-status").json()["state"] == "stopped"
    assert client.get(base + "/latest").json() is None
    assert (
        client.get(base + "/locate-requests/" + req["id"]).json()["failureCode"]
        == "sharing_stopped"
    )
    assert client.post(base + "/locate-requests", headers=admin["headers"]).status_code == 409
    assert client.get("/api/v1/device/config", headers=headers).status_code == 401


def test_missing_capture_does_not_block_valid_points(client, admin):
    elder, token = enroll_elder(client, admin, "模拟老人", "qa-partial-device")
    point = dict(latitude=30, longitude=120, accuracyM=8, batteryPct=75)
    now = datetime.now(timezone.utc).isoformat()
    resp = client.post(
        "/api/v1/device/locations:batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"points": [dict(point, sequence=8), dict(point, sequence=9, capturedAt=now)]},
    )
    assert resp.status_code == 200
    assert resp.json()["acceptedSequences"] == [9]
    assert resp.json()["rejected"][0]["sequence"] == 8
    assert "capturedAt" in resp.json()["rejected"][0]["reason"]
    latest = client.get(f"/api/v1/families/{admin['family_id']}/elders/{elder}/latest")
    assert datetime.fromisoformat(latest.json()["capturedAt"]) == datetime.fromisoformat(now)
