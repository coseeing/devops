from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import IPv4Address

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi.testclient import TestClient
from vms_portal.audit import AuditLogger
from vms_portal.config import Settings
from vms_portal.costs import InstanceCost
from vms_portal.ec2 import VmInstance
from vms_portal.secrets import AuthSnapshot, CredentialRecord
from vms_portal.web import create_app


class FakeSecretCache:
    def __init__(self) -> None:
        hasher = PasswordHasher()
        self.snapshot = AuthSnapshot(
            {
                "admin": CredentialRecord("admin", "admin", hasher.hash("admin-pass")),
                "user": CredentialRecord("user", "user", hasher.hash("user-pass")),
            },
            "s" * 64,
            1,
            "v1",
        )
        self.hasher = hasher

    def load_startup(self):
        return self.snapshot

    def snapshot_for_auth(self):
        return self.snapshot

    def verify_password(self, username, password):
        record = self.snapshot.credentials.get(username)
        if not record:
            return None
        try:
            return (
                record if self.hasher.verify(record.password_hash, password) else None
            )
        except VerificationError:
            return None


class FakeEc2:
    def __init__(self) -> None:
        self.vm = VmInstance(
            "i-123",
            "windows-demo",
            IPv4Address("198.51.100.9"),
            "m5.xlarge",
            "running",
            datetime(2026, 8, 20, tzinfo=UTC),
        )
        self.list_calls = 0
        self.stop_calls = 0

    def list_managed(self):
        self.list_calls += 1
        return [self.vm]

    def find_managed_by_public_ip(self, ip):
        return self.vm if ip == self.vm.public_ip else None

    def stop(self, instance_id, expected_public_ip=None):
        self.stop_calls += 1
        return self.vm

    def start(self, instance_id, expected_public_ip=None):
        return self.vm


class FakeCosts:
    def get_costs(self, instance_ids, now):
        return {
            instance_id: InstanceCost(
                instance_id, 1.25, "USD", False, datetime(2026, 8, 20, tzinfo=UTC)
            )
            for instance_id in instance_ids
        }


def make_client():
    ec2 = FakeEc2()
    audit_events = []
    app = create_app(
        Settings.from_env({"AUTH_SECRET_ID": "test"}),
        secret_cache=FakeSecretCache(),
        ec2_service=ec2,
        cost_service=FakeCosts(),
        audit_logger=AuditLogger(audit_events.append),
        clock=lambda: 1_000.0,
    )
    return TestClient(app, base_url="https://testserver"), ec2, audit_events


def login(client: TestClient, username: str, password: str) -> None:
    page = client.get("/login")
    token = page.cookies["vms_portal_login_csrf"]
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_login_requires_csrf_and_sets_secure_session_cookie() -> None:
    client, _, _ = make_client()
    assert (
        client.post(
            "/login",
            data={"username": "admin", "password": "admin-pass", "csrf_token": "bad"},
        ).status_code
        == 403
    )

    page = client.get("/login")
    token = page.cookies["vms_portal_login_csrf"]
    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin-pass", "csrf_token": token},
        follow_redirects=False,
    )

    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.headers["x-frame-options"] == "DENY"


def test_admin_home_lists_managed_instances() -> None:
    client, ec2, _ = make_client()
    login(client, "admin", "admin-pass")

    response = client.get("/")

    assert response.status_code == 200
    assert "vms_portal_session=" in response.headers["set-cookie"]
    assert "windows-demo" in response.text
    assert "198.51.100.9" in response.text
    assert 'action="/instances/i-123/stop"' in response.text
    assert 'data-confirm="停止 windows-demo？"' in response.text
    assert "1.25 USD" in response.text
    assert ec2.list_calls == 1


def test_user_home_never_lists_and_exact_ip_lookup_returns_one_vm() -> None:
    client, ec2, _ = make_client()
    login(client, "user", "user-pass")

    home = client.get("/")
    assert "輸入 Public IPv4" in home.text
    assert "windows-demo" not in home.text
    assert ec2.list_calls == 0

    csrf = (
        home.cookies.get("vms_portal_session_csrf")
        or client.cookies["vms_portal_session_csrf"]
    )
    result = client.post(
        "/lookup", data={"public_ip": "198.51.100.9", "csrf_token": csrf}
    )
    assert result.status_code == 200
    assert "windows-demo" in result.text
    assert 'action="/instances/i-123/stop"' in result.text
    assert 'name="public_ip" value="198.51.100.9"' in result.text
    assert "1.25 USD" in result.text


def test_user_invalid_or_unknown_ip_gets_generic_message() -> None:
    client, _, _ = make_client()
    login(client, "user", "user-pass")
    home = client.get("/")
    csrf = (
        home.cookies.get("vms_portal_session_csrf")
        or client.cookies["vms_portal_session_csrf"]
    )

    invalid = client.post(
        "/lookup", data={"public_ip": "not-an-ip", "csrf_token": csrf}
    )
    missing = client.post(
        "/lookup", data={"public_ip": "203.0.113.8", "csrf_token": csrf}
    )

    assert "找不到符合條件的機器" in invalid.text
    assert "找不到符合條件的機器" in missing.text


def test_logout_requires_csrf_and_clears_session() -> None:
    client, _, _ = make_client()
    login(client, "admin", "admin-pass")
    home = client.get("/")
    csrf = (
        home.cookies.get("vms_portal_session_csrf")
        or client.cookies["vms_portal_session_csrf"]
    )

    assert client.post("/logout", data={"csrf_token": "bad"}).status_code == 403
    response = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)

    assert response.status_code == 303
    assert 'vms_portal_session=""' in response.headers["set-cookie"]


def test_stop_writes_audit_before_and_after_mutation() -> None:
    client, ec2, events = make_client()
    login(client, "admin", "admin-pass")
    client.get("/")
    csrf = client.cookies["vms_portal_session_csrf"]

    response = client.post(
        "/instances/i-123/stop", data={"csrf_token": csrf}, follow_redirects=False
    )

    assert response.status_code == 303
    assert ec2.stop_calls == 1
    assert '"event":"vm.stop.accepted"' in events[-2]
    assert '"event":"vm.stop.succeeded"' in events[-1]
