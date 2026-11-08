import time as time_module
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient


def enroll_elder(client: TestClient, admin: dict, name: str, installation_id: str) -> tuple[str, str]:
    family_id = admin["family_id"]
    elder = client.post(
        f"/api/v1/families/{family_id}/elders",
        headers=admin["headers"],
        json={"displayName": name, "timezone": "Asia/Shanghai"},
    ).json()
    enrollment = client.post(
        f"/api/v1/families/{family_id}/elders/{elder['id']}/device-enrollments",
        headers=admin["headers"],
    ).json()
    device = client.post(
        "/api/v1/device/enroll",
        json={"code": enrollment["code"], "installationId": installation_id},
    ).json()
    return elder["id"], device["deviceToken"]


def test_device_upload_daily_track_and_geofence(client: TestClient, admin: dict):
    family_id = admin["family_id"]
    elder_response = client.post(
        f"/api/v1/families/{family_id}/elders",
        headers=admin["headers"],
        json={"displayName": "张奶奶", "timezone": "Asia/Shanghai"},
    )
    assert elder_response.status_code == 201
    elder_id = elder_response.json()["id"]

    fence = client.put(
        f"/api/v1/families/{family_id}/elders/{elder_id}/home-geofence",
        headers=admin["headers"],
        json={"latitude": 31.2304, "longitude": 121.4737},
    )
    assert fence.status_code == 200
    enrollment = client.post(
        f"/api/v1/families/{family_id}/elders/{elder_id}/device-enrollments",
        headers=admin["headers"],
    ).json()
    device = client.post(
        "/api/v1/device/enroll",
        json={"code": enrollment["code"], "installationId": "test-phone-0001", "model": "iQOO Z10 Turbo+", "appVersion": "0.1.0"},
    )
    assert device.status_code == 200
    token = device.json()["deviceToken"]

    now = datetime.now(timezone.utc).replace(microsecond=0)
    points = [
        {"sequence": 1, "capturedAt": (now - timedelta(seconds=30)).isoformat(), "latitude": 31.30, "longitude": 121.55, "accuracyM": 8, "speedMps": 1.2, "batteryPct": 80, "isCharging": False, "isMock": False},
        {"sequence": 2, "capturedAt": now.isoformat(), "latitude": 31.31, "longitude": 121.56, "accuracyM": 9, "speedMps": 1.1, "batteryPct": 79, "isCharging": False, "isMock": False},
    ]
    upload = client.post(
        "/api/v1/device/locations:batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"points": points, "appVersion": "0.1.0"},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["acceptedSequences"] == [1, 2]

    duplicate = client.post(
        "/api/v1/device/locations:batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"points": points},
    )
    assert duplicate.json()["duplicateSequences"] == [1, 2]

    latest = client.get(f"/api/v1/families/{family_id}/elders/{elder_id}/latest")
    assert latest.status_code == 200
    assert latest.json()["batteryPct"] == 79

    days = client.get(f"/api/v1/families/{family_id}/elders/{elder_id}/track-days")
    assert days.status_code == 200
    assert days.json()[0]["validPointCount"] == 2
    detail = client.get(f"/api/v1/families/{family_id}/elders/{elder_id}/track-days/{days.json()[0]['date']}")
    assert detail.status_code == 200
    assert len(detail.json()["segments"]) == 1
    assert len(detail.json()["segments"][0]["points"]) == 2


def test_device_status_events_and_home_config(client: TestClient, admin: dict):
    from app.database import SessionLocal
    from app.models import ElderDevice, EventLog

    family_id = admin["family_id"]
    elder = client.post(
        f"/api/v1/families/{family_id}/elders",
        headers=admin["headers"],
        json={"displayName": "周奶奶"},
    ).json()
    status_url = f"/api/v1/families/{family_id}/elders/{elder['id']}/device-status"
    assert client.get(status_url).json() == {
        "state": "unbound",
        "model": None,
        "appVersion": None,
        "boundAt": None,
        "lastSeenAt": None,
        "lastLocationAt": None,
        "backgroundLocationGranted": None,
        "batteryOptimizationIgnored": None,
        "locationServicesEnabled": None,
        "lastServiceError": None,
    }

    enrollment = client.post(
        f"/api/v1/families/{family_id}/elders/{elder['id']}/device-enrollments",
        headers=admin["headers"],
    ).json()
    enrolled = client.post(
        "/api/v1/device/enroll",
        json={
            "code": enrollment["code"],
            "installationId": "status-phone-0001",
            "model": "Test Phone",
            "appVersion": "0.2.0",
        },
    )
    assert enrolled.status_code == 200
    token = enrolled.json()["deviceToken"]
    waiting = client.get(status_url).json()
    assert waiting["state"] == "bound_waiting_location"
    assert waiting["model"] == "Test Phone"
    assert waiting["appVersion"] == "0.2.0"
    assert waiting["boundAt"]

    config = client.get(
        "/api/v1/device/config", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert config["configVersion"] == 2
    assert config["homeGeofence"] is None

    fence = client.put(
        f"/api/v1/families/{family_id}/elders/{elder['id']}/home-geofence",
        headers=admin["headers"],
        json={"latitude": 31.2304, "longitude": 121.4737},
    )
    assert fence.status_code == 200
    assert fence.json()["status"] == "unknown"
    assert fence.json()["lastValidLocationAt"] is None
    assert fence.json()["updatedAt"]
    home = client.get(
        "/api/v1/device/config", headers={"Authorization": f"Bearer {token}"}
    ).json()["homeGeofence"]
    assert home["radiusM"] == 5000
    assert datetime.fromisoformat(home["updatedAt"]).replace(tzinfo=None) == datetime.fromisoformat(
        fence.json()["updatedAt"]
    ).replace(tzinfo=None)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    upload = client.post(
        "/api/v1/device/locations:batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"points": [{
            "sequence": 1,
            "capturedAt": now.isoformat(),
            "latitude": 31.2304,
            "longitude": 121.4737,
            "accuracyM": 8,
            "batteryPct": 82,
        }]},
    )
    assert upload.status_code == 200
    assert client.get(status_url).json()["state"] == "online"

    with SessionLocal.begin() as db:
        device = db.query(ElderDevice).filter(ElderDevice.active.is_(True)).one()
        device.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        event_types = [item.event_type for item in db.query(EventLog).all()]
        assert "device.enrolled" in event_types
        assert "home-geofence.updated" in event_types
    assert client.get(status_url).json()["state"] == "stale"


def test_on_demand_locate_long_poll_to_completed_result(client: TestClient, admin: dict):
    family_id = admin["family_id"]
    elder_id, token = enroll_elder(client, admin, "陈奶奶", "locate-command-phone")
    locate_url = f"/api/v1/families/{family_id}/elders/{elder_id}/locate-requests"
    device_headers = {"Authorization": f"Bearer {token}"}

    def wait_for_command():
        return client.post(
            "/api/v1/device/commands:wait?timeoutSeconds=5",
            headers=device_headers,
            json={
                "appVersion": "0.3.0",
                "backgroundLocationGranted": True,
                "batteryOptimizationIgnored": True,
                "locationServicesEnabled": True,
            },
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(wait_for_command)
        time_module.sleep(0.2)
        created = client.post(locate_url, headers=admin["headers"])
        command = waiting.result(timeout=7)

    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert command.status_code == 200, command.text
    assert command.json() == {
        "id": request_id,
        "type": "locate_now",
        "expiresAt": created.json()["expiresAt"],
    }

    ack = client.post(
        f"/api/v1/device/locate-requests/{request_id}/ack", headers=device_headers
    )
    assert ack.json()["state"] == "acknowledged"

    captured_at = datetime.now(timezone.utc)
    uploaded = client.post(
        "/api/v1/device/locations:batch",
        headers=device_headers,
        json={
            "appVersion": "0.3.0",
            "points": [
                {
                    "sequence": 1,
                    "capturedAt": captured_at.isoformat(),
                    "latitude": 31.2304,
                    "longitude": 121.4737,
                    "accuracyM": 9,
                    "batteryPct": 76,
                    "locateRequestId": request_id,
                }
            ],
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    result = client.get(f"{locate_url}/{request_id}").json()
    assert result["state"] == "completed"
    assert result["result"]["accuracyM"] == 9

    device_status = client.get(
        f"/api/v1/families/{family_id}/elders/{elder_id}/device-status"
    ).json()
    assert device_status["appVersion"] == "0.3.0"
    assert device_status["lastLocationAt"]
    assert device_status["backgroundLocationGranted"] is True
    assert device_status["batteryOptimizationIgnored"] is True
    assert device_status["locationServicesEnabled"] is True

    limited = client.post(locate_url, headers=admin["headers"])
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0


def test_on_demand_locate_rejects_low_accuracy_and_reports_failure(
    client: TestClient, admin: dict
):
    family_id = admin["family_id"]
    elder_id, token = enroll_elder(client, admin, "孙爷爷", "low-accuracy-phone")
    locate_url = f"/api/v1/families/{family_id}/elders/{elder_id}/locate-requests"
    device_headers = {"Authorization": f"Bearer {token}"}
    created = client.post(locate_url, headers=admin["headers"])
    request_id = created.json()["id"]
    client.post(f"/api/v1/device/locate-requests/{request_id}/ack", headers=device_headers)

    uploaded = client.post(
        "/api/v1/device/locations:batch",
        headers=device_headers,
        json={
            "points": [
                {
                    "sequence": 1,
                    "capturedAt": datetime.now(timezone.utc).isoformat(),
                    "latitude": 31.2304,
                    "longitude": 121.4737,
                    "accuracyM": 550,
                    "batteryPct": 66,
                    "locateRequestId": request_id,
                }
            ]
        },
    )
    assert uploaded.status_code == 200
    assert client.get(f"{locate_url}/{request_id}").json()["state"] == "acknowledged"

    failed = client.post(
        f"/api/v1/device/locate-requests/{request_id}/fail",
        headers=device_headers,
        json={"failureCode": "low_accuracy"},
    )
    assert failed.json() == {"state": "failed", "failureCode": "low_accuracy"}
    assert client.get(f"{locate_url}/{request_id}").json()["state"] == "failed"


def test_family_tenant_isolation(client: TestClient, admin: dict):
    from app.database import SessionLocal
    from app.models import Family, FamilyMembership, User
    from app.security import hash_password

    protected_elder = client.post(
        f"/api/v1/families/{admin['family_id']}/elders",
        headers=admin["headers"],
        json={"displayName": "受保护老人"},
    ).json()

    with SessionLocal.begin() as db:
        second_family = Family(name="另一个家庭")
        second_user = User(email="two@example.com", password_hash=hash_password("another-secure-password"))
        db.add_all([second_family, second_user])
        db.flush()
        db.add(FamilyMembership(family_id=second_family.id, user_id=second_user.id, role="admin"))
        second_family_id = str(second_family.id)

    client.post("/api/v1/auth/logout", headers=admin["headers"])
    login = client.post("/api/v1/auth/login", json={"email": "two@example.com", "password": "another-secure-password"})
    assert login.status_code == 200
    forbidden = client.get(f"/api/v1/families/{admin['family_id']}/elders")
    assert forbidden.status_code == 404
    forbidden_status = client.get(
        f"/api/v1/families/{admin['family_id']}/elders/{protected_elder['id']}/device-status"
    )
    assert forbidden_status.status_code == 404
    own = client.get(f"/api/v1/families/{second_family_id}/elders")
    assert own.status_code == 200


def test_rejects_mock_and_bad_accuracy_from_replay(client: TestClient, admin: dict):
    family_id = admin["family_id"]
    elder = client.post(
        f"/api/v1/families/{family_id}/elders",
        headers=admin["headers"],
        json={"displayName": "李爷爷"},
    ).json()
    enrollment = client.post(
        f"/api/v1/families/{family_id}/elders/{elder['id']}/device-enrollments",
        headers=admin["headers"],
    ).json()
    token = client.post("/api/v1/device/enroll", json={"code": enrollment["code"], "installationId": "phone-0002"}).json()["deviceToken"]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    client.post(
        "/api/v1/device/locations:batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"points": [
            {"sequence": 1, "capturedAt": now.isoformat(), "latitude": 31, "longitude": 121, "accuracyM": 300, "batteryPct": 50, "isCharging": False, "isMock": False},
            {"sequence": 2, "capturedAt": (now + timedelta(seconds=1)).isoformat(), "latitude": 31, "longitude": 121, "accuracyM": 5, "batteryPct": 50, "isCharging": False, "isMock": True},
        ]},
    )
    days = client.get(f"/api/v1/families/{family_id}/elders/{elder['id']}/track-days").json()
    detail = client.get(f"/api/v1/families/{family_id}/elders/{elder['id']}/track-days/{days[0]['date']}").json()
    assert detail["skippedPointCount"] == 2
    assert detail["segments"] == []
    assert client.get(
        f"/api/v1/families/{family_id}/elders/{elder['id']}/latest"
    ).json() is None


def test_cross_midnight_days_and_long_gap_segments(client: TestClient, admin: dict):
    family_id = admin["family_id"]
    elder_id, token = enroll_elder(client, admin, "王爷爷", "cross-day-phone")
    shanghai = ZoneInfo("Asia/Shanghai")
    yesterday = datetime.now(shanghai).date() - timedelta(days=1)
    before_midnight = datetime.combine(yesterday, time(23, 59, 30), shanghai)
    after_midnight = before_midnight + timedelta(minutes=1)
    after_gap = after_midnight + timedelta(minutes=11)
    points = [
        {
            "sequence": sequence,
            "capturedAt": captured_at.isoformat(),
            "latitude": 31.2304 + sequence / 10_000,
            "longitude": 121.4737,
            "accuracyM": 10,
            "batteryPct": 70 - sequence,
        }
        for sequence, captured_at in enumerate(
            [before_midnight, after_midnight, after_gap], start=1
        )
    ]
    upload = client.post(
        "/api/v1/device/locations:batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"points": points},
    )
    assert upload.status_code == 200

    days = client.get(
        f"/api/v1/families/{family_id}/elders/{elder_id}/track-days"
    ).json()
    assert [day["date"] for day in days] == [
        after_midnight.date().isoformat(),
        before_midnight.date().isoformat(),
    ]
    detail = client.get(
        f"/api/v1/families/{family_id}/elders/{elder_id}/track-days/"
        f"{after_midnight.date().isoformat()}"
    ).json()
    assert detail["summary"]["gapCount"] == 1
    assert len(detail["segments"]) == 2
    assert detail["segments"][1]["gapBeforeSeconds"] == 660


def test_geofence_hysteresis_requires_consecutive_points(client: TestClient, admin: dict):
    from app.database import SessionLocal
    from app.models import NotificationOutbox

    family_id = admin["family_id"]
    elder_id, token = enroll_elder(client, admin, "赵奶奶", "geofence-phone")
    fence_url = f"/api/v1/families/{family_id}/elders/{elder_id}/home-geofence"
    assert client.put(
        fence_url,
        headers=admin["headers"],
        json={"latitude": 31.2304, "longitude": 121.4737},
    ).status_code == 200

    # Approximately 1.1 km, 6.7 km and 4.7 km north of the home point.
    inside, outside, band = 31.2404, 31.2904, 31.2727
    latitudes = [inside, inside, outside, band, outside, outside, inside, band, inside, inside]
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    points = [
        {
            "sequence": sequence,
            "capturedAt": (start + timedelta(seconds=30 * sequence)).isoformat(),
            "latitude": latitude,
            "longitude": 121.4737,
            "accuracyM": 8,
            "batteryPct": 90,
        }
        for sequence, latitude in enumerate(latitudes, start=1)
    ]
    assert client.post(
        "/api/v1/device/locations:batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"points": points[:5]},
    ).status_code == 200
    assert client.get(fence_url).json()["status"] == "inside"

    assert client.post(
        "/api/v1/device/locations:batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"points": points[5:6]},
    ).status_code == 200
    assert client.get(fence_url).json()["status"] == "outside"

    assert client.post(
        "/api/v1/device/locations:batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"points": points[6:9]},
    ).status_code == 200
    assert client.get(fence_url).json()["status"] == "outside"
    assert client.post(
        "/api/v1/device/locations:batch",
        headers={"Authorization": f"Bearer {token}"},
        json={"points": points[9:]},
    ).status_code == 200
    assert client.get(fence_url).json()["status"] == "inside"

    with SessionLocal() as db:
        assert db.query(NotificationOutbox).count() == 1
