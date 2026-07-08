"""Load and validate targets.yaml and Slack environment settings."""

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class Defaults(BaseModel):
    check_interval_seconds: float = Field(default=60, gt=0)
    timeout_seconds: float = Field(default=10, gt=0)
    max_attempts: int = Field(default=3, ge=1)
    retry_delay_seconds: float = Field(default=5, ge=0)
    remind_interval_minutes: float = Field(default=30, gt=0)


class Target(BaseModel):
    name: str
    url: str

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"url must start with http:// or https://, got {value!r}")
        return value


class Config(BaseModel):
    defaults: Defaults = Field(default_factory=Defaults)
    targets: list[Target] = Field(min_length=1)

    @model_validator(mode="after")
    def target_names_must_be_unique(self) -> "Config":
        seen: set[str] = set()
        duplicates = {t.name for t in self.targets if t.name in seen or seen.add(t.name)}
        if duplicates:
            raise ValueError(f"duplicate target names: {', '.join(sorted(duplicates))}")
        return self


class SlackSettings(BaseModel):
    bot_token: str
    channel_id: str


def load_config(path: Path) -> Config:
    with open(path) as f:
        data = yaml.safe_load(f)
    return Config.model_validate(data)


def load_slack_settings(env: Mapping[str, str]) -> SlackSettings:
    missing = [key for key in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID") if not env.get(key)]
    if missing:
        raise ValueError(f"missing required environment variables: {', '.join(missing)}")
    return SlackSettings(bot_token=env["SLACK_BOT_TOKEN"], channel_id=env["SLACK_CHANNEL_ID"])
