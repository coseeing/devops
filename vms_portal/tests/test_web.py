from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import IPv4Address

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi.testclient import TestClient
from vms_portal.assignments import Assignment
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
            "i-1234567890abcdef0",
            "windows-demo",
            IPv4Address("10.0.0.4"),
            IPv4Address("198.51.100.9"),
            "m5.xlarge",
            "running",
            datetime(2026, 8, 20, tzinfo=UTC),
        )
        self.list_calls = 0
        self.stop_calls = 0
        self.lookup_calls = []

    def list_managed(self):
        self.list_calls += 1
        return [self.vm]

    def find_managed_by_instance_id(self, instance_id):
        self.lookup_calls.append(instance_id)
        return self.vm if instance_id == self.vm.instance_id else None

    def stop(self, instance_id, expected_public_ip=None):
        self.stop_calls += 1
        return self.vm

    def start(self, instance_id, expected_public_ip=None):
        return self.vm


class FakeCosts:
    def get_costs(self, vms, now):
        return {
            vm.instance_id: InstanceCost(
                vm.instance_id,
                "ready",
                Decimal("1.25"),
                "USD",
                datetime(2026, 7, 1, tzinfo=UTC).date(),
                datetime(2026, 8, 20, tzinfo=UTC).date(),
                datetime(2026, 8, 20, tzinfo=UTC),
                "query-123",
            )
            for vm in vms
        }


class FakeCostsNotReady:
    def get_costs(self, vms, now):
        return {
            vm.instance_id: InstanceCost(
                vm.instance_id,
                "not_ready",
                None,
                "USD",
                None,
                None,
                datetime(2026, 8, 20, tzinfo=UTC),
                "query-123",
            )
            for vm in vms
        }


class FakeCostsFailed:
    def get_costs(self, vms, now):
        return {
            vm.instance_id: InstanceCost(
                vm.instance_id,
                "failed",
                None,
                "USD",
                None,
                None,
                datetime(2026, 8, 20, tzinfo=UTC),
                "query-123",
            )
            for vm in vms
        }


class FakeAssignments:
    def __init__(self) -> None:
        self.values = {
            "i-1234567890abcdef0": Assignment(
                "i-1234567890abcdef0",
                "Original owner",
                datetime(2026, 8, 20, tzinfo=UTC),
                "admin",
            )
        }
        self.upsert_calls = []

    def get_many(self, instance_ids):
        return {key: self.values[key] for key in instance_ids if key in self.values}

    def upsert(self, instance_id, assignee, *, updated_by, updated_at):
        self.upsert_calls.append((instance_id, assignee, updated_by))
        assignment = Assignment(instance_id, assignee, updated_at, updated_by)
        self.values[instance_id] = assignment
        return assignment


def make_client(cost_service=None, assignment_repository=None):
    ec2 = FakeEc2()
    audit_events = []
    app = create_app(
        Settings.from_env({"AUTH_SECRET_ID": "test"}),
        secret_cache=FakeSecretCache(),
        ec2_service=ec2,
        cost_service=cost_service or FakeCosts(),
        assignment_repository=assignment_repository or FakeAssignments(),
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
    assert "i-1234567890abcdef0" in response.text
    assert "Original owner" in response.text
    assert "10.0.0.4" in response.text
    assert "198.51.100.9" in response.text
    assert 'action="/instances/i-1234567890abcdef0/stop"' in response.text
    assert 'data-confirm="停止 windows-demo？"' in response.text
    assert "最近 60 天 EC2 成本" in response.text
    assert "1.25 USD" in response.text
    assert "2026-07-01～2026-08-20" in response.text
    assert "EIP" not in response.text
    assert ec2.list_calls == 1


def test_user_home_never_lists_and_exact_instance_id_lookup_returns_one_vm() -> None:
    client, ec2, _ = make_client()
    login(client, "user", "user-pass")

    home = client.get("/")
    assert "輸入 Instance ID" in home.text
    assert "windows-demo" not in home.text
    assert ec2.list_calls == 0

    csrf = (
        home.cookies.get("vms_portal_session_csrf")
        or client.cookies["vms_portal_session_csrf"]
    )
    result = client.post(
        "/lookup",
        data={"instance_id": "i-1234567890abcdef0", "csrf_token": csrf},
    )
    assert result.status_code == 200
    assert "windows-demo" in result.text
    assert "i-1234567890abcdef0" in result.text
    assert "10.0.0.4" in result.text
    assert "198.51.100.9" not in result.text
    assert "Original owner" not in result.text
    assert 'action="/instances/i-1234567890abcdef0/stop"' in result.text
    assert 'name="public_ip"' not in result.text
    assert "1.25 USD" in result.text
    assert "2026-07-01～2026-08-20" in result.text
    assert "EIP" not in result.text


def test_cur_not_ready_is_distinct_from_zero_cost() -> None:
    client, _, _ = make_client(FakeCostsNotReady())
    login(client, "admin", "admin-pass")

    response = client.get("/")

    assert "成本報表尚未準備完成" in response.text
    assert "EIP" not in response.text


def test_cur_query_failure_is_shown_separately() -> None:
    client, _, _ = make_client(FakeCostsFailed())
    login(client, "admin", "admin-pass")

    response = client.get("/")

    assert "成本查詢失敗" in response.text
    assert "成本報表尚未準備完成" not in response.text


def test_user_invalid_or_unknown_instance_id_gets_generic_message() -> None:
    client, _, _ = make_client()
    login(client, "user", "user-pass")
    home = client.get("/")
    csrf = (
        home.cookies.get("vms_portal_session_csrf")
        or client.cookies["vms_portal_session_csrf"]
    )

    invalid = client.post(
        "/lookup", data={"instance_id": "not-an-id", "csrf_token": csrf}
    )
    missing = client.post(
        "/lookup", data={"instance_id": "i-00000000000000000", "csrf_token": csrf}
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
        "/instances/i-1234567890abcdef0/stop",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert ec2.stop_calls == 1
    assert '"event":"vm.stop.accepted"' in events[-2]
    assert '"event":"vm.stop.succeeded"' in events[-1]


def test_admin_can_update_assignment_with_csrf_and_audit() -> None:
    assignments = FakeAssignments()
    client, ec2, events = make_client(assignment_repository=assignments)
    login(client, "admin", "admin-pass")
    client.get("/")
    csrf = client.cookies["vms_portal_session_csrf"]

    response = client.post(
        "/instances/i-1234567890abcdef0/assignment",
        data={"csrf_token": csrf, "assignee": "Anson"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert ec2.lookup_calls == ["i-1234567890abcdef0"]
    assert assignments.upsert_calls == [("i-1234567890abcdef0", "Anson", "admin")]
    assert '"event":"vm.assignment.updated"' in events[-1]
    assert '"assignee":"Anson"' in events[-1]


def test_assignment_update_is_admin_only_and_requires_csrf() -> None:
    assignments = FakeAssignments()
    client, _, _ = make_client(assignment_repository=assignments)
    login(client, "admin", "admin-pass")
    assert (
        client.post(
            "/instances/i-1234567890abcdef0/assignment",
            data={"csrf_token": "bad", "assignee": "Anson"},
        ).status_code
        == 403
    )

    client, _, _ = make_client(assignment_repository=assignments)
    login(client, "user", "user-pass")
    home = client.get("/")
    csrf = (
        home.cookies.get("vms_portal_session_csrf")
        or client.cookies["vms_portal_session_csrf"]
    )
    response = client.post(
        "/instances/i-1234567890abcdef0/assignment",
        data={"csrf_token": csrf, "assignee": "Anson"},
    )

    assert response.status_code == 403
    assert assignments.upsert_calls == []
