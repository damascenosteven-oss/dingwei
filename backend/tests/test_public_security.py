import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize(
    ("path", "headers", "payload", "expected_status"),
    [
        (
            "/api/v1/bootstrap",
            {"X-Bootstrap-Token": "wrong"},
            {"familyName": "test", "email": "new@example.com", "password": "password-long"},
            403,
        ),
        (
            "/api/v1/auth/login",
            {},
            {"email": "missing@example.com", "password": "wrong"},
            401,
        ),
        (
            "/api/v1/auth/accept-invitation",
            {},
            {"token": "missing", "password": "password-long"},
            400,
        ),
        (
            "/api/v1/device/enroll",
            {},
            {"code": "000000", "installationId": "missing1"},
            400,
        ),
    ],
)
def test_sensitive_endpoint_rate_limit(
    client: TestClient,
    path: str,
    headers: dict[str, str],
    payload: dict[str, str],
    expected_status: int,
):
    for _ in range(10):
        response = client.post(path, headers=headers, json=payload)
        assert response.status_code == expected_status

    limited = client.post(path, headers=headers, json=payload)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert limited.headers["Cache-Control"] == "no-store"


def test_api_docs_can_be_disabled():
    application = create_app(Settings(expose_api_docs=False))
    with TestClient(application) as client:
        assert client.get("/api/docs").status_code == 404
        assert client.get("/api/openapi.json").status_code == 404
