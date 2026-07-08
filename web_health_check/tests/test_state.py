from datetime import datetime, timedelta, timezone

from health_check.checker import CheckResult
from health_check.config import Target
from health_check.state import NotificationKind, Status, TargetState

TARGET = Target(name="Frontend", url="https://example.com/")
T0 = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
REMIND_INTERVAL = timedelta(minutes=30)


def ok_result():
    return CheckResult(target=TARGET, ok=True, attempts=1, status_code=200)


def fail_result(reason="ConnectError: connection refused"):
    return CheckResult(target=TARGET, ok=False, attempts=3, reason=reason)


def make_state():
    return TargetState(target=TARGET, remind_interval=REMIND_INTERVAL)


def test_first_success_is_silent():
    state = make_state()

    assert state.transition(ok_result(), T0) is None
    assert state.status is Status.UP


def test_first_failure_notifies_down():
    state = make_state()

    notification = state.transition(fail_result(), T0)

    assert state.status is Status.DOWN
    assert notification.kind is NotificationKind.DOWN
    assert notification.target is TARGET
    assert "connection refused" in notification.reason
    assert notification.attempts == 3


def test_up_to_down_notifies_with_reason():
    state = make_state()
    state.transition(ok_result(), T0)

    notification = state.transition(fail_result("HTTP 503"), T0 + timedelta(minutes=1))

    assert notification.kind is NotificationKind.DOWN
    assert notification.reason == "HTTP 503"


def test_down_to_up_notifies_recovery_with_duration():
    state = make_state()
    state.transition(fail_result(), T0)

    notification = state.transition(ok_result(), T0 + timedelta(minutes=45))

    assert state.status is Status.UP
    assert notification.kind is NotificationKind.RECOVERY
    assert notification.downtime == timedelta(minutes=45)


def test_still_down_before_remind_interval_is_silent():
    state = make_state()
    state.transition(fail_result(), T0)

    assert state.transition(fail_result(), T0 + timedelta(minutes=29)) is None


def test_still_down_after_remind_interval_sends_reminder():
    state = make_state()
    state.transition(fail_result(), T0)

    notification = state.transition(fail_result(), T0 + timedelta(minutes=31))

    assert notification.kind is NotificationKind.REMINDER
    assert notification.downtime == timedelta(minutes=31)


def test_reminder_resets_its_own_timer():
    state = make_state()
    state.transition(fail_result(), T0)
    state.transition(fail_result(), T0 + timedelta(minutes=31))

    assert state.transition(fail_result(), T0 + timedelta(minutes=45)) is None
    later = state.transition(fail_result(), T0 + timedelta(minutes=62))
    assert later.kind is NotificationKind.REMINDER


def test_staying_up_is_silent():
    state = make_state()
    state.transition(ok_result(), T0)

    assert state.transition(ok_result(), T0 + timedelta(minutes=1)) is None
