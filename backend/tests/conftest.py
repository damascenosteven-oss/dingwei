import os

os.environ["DATABASE_URL"] = "sqlite:///./test-family-location.db"
os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ["BOOTSTRAP_TOKEN"] = "test-bootstrap-token"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_database():
    app.state.sensitive_rate_limiter.clear()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    app.state.sensitive_rate_limiter.clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin(client: TestClient):
    response = client.post(
        "/api/v1/bootstrap",
        headers={"X-Bootstrap-Token": "test-bootstrap-token"},
        json={"familyName": "测试家庭", "email": "one@example.com", "password": "very-secure-password"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return {
        "family_id": payload["families"][0]["id"],
        "csrf": payload["csrfToken"],
        "headers": {"X-CSRF-Token": payload["csrfToken"]},
    }
