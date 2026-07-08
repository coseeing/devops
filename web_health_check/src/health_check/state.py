"""Per-target state machine deciding when to notify."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto

from health_check.checker import CheckResult
from health_check.config import Target


class Status(Enum):
    UNKNOWN = auto()
    UP = auto()
    DOWN = auto()


class NotificationKind(Enum):
    DOWN = auto()
    REMINDER = auto()
    RECOVERY = auto()


@dataclass
class Notification:
    kind: NotificationKind
    target: Target
    reason: str | None = None
    downtime: timedelta | None = None
    attempts: int | None = None


class TargetState:
    def __init__(self, target: Target, remind_interval: timedelta):
        self.target = target
        self.remind_interval = remind_interval
        self.status = Status.UNKNOWN
        self.down_since: datetime | None = None
        self.last_notified: datetime | None = None

    def transition(self, result: CheckResult, now: datetime) -> Notification | None:
        if result.ok:
            was_down = self.status is Status.DOWN
            down_since = self.down_since
            self.status = Status.UP
            self.down_since = None
            self.last_notified = None
            if was_down:
                return Notification(
                    kind=NotificationKind.RECOVERY,
                    target=self.target,
                    downtime=now - down_since,
                )
            return None

        if self.status is not Status.DOWN:
            self.status = Status.DOWN
            self.down_since = now
            self.last_notified = now
            return Notification(
                kind=NotificationKind.DOWN,
                target=self.target,
                reason=result.reason,
                attempts=result.attempts,
            )

        if now - self.last_notified >= self.remind_interval:
            self.last_notified = now
            return Notification(
                kind=NotificationKind.REMINDER,
                target=self.target,
                reason=result.reason,
                downtime=now - self.down_since,
            )
        return None
