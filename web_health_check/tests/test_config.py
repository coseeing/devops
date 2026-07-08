import pytest

from health_check.config import Config, SlackSettings, load_config, load_slack_settings

FULL_YAML = """\
defaults:
  check_interval_seconds: 30
  timeout_seconds: 5
  max_attempts: 2
  retry_delay_seconds: 1
  remind_interval_minutes: 10
targets:
  - name: Frontend
    url: https://example.com
  - name: Backend API
    url: https://api.example.com/health
"""

MINIMAL_YAML = """\
targets:
  - name: Frontend
    url: https://example.com
"""


def write_yaml(tmp_path, content):
    path = tmp_path / "targets.yaml"
    path.write_text(content)
    return path


def test_load_config_parses_targets_and_defaults(tmp_path):
    config = load_config(write_yaml(tmp_path, FULL_YAML))

    assert isinstance(config, Config)
    assert config.defaults.check_interval_seconds == 30
    assert config.defaults.timeout_seconds == 5
    assert config.defaults.max_attempts == 2
    assert config.defaults.retry_delay_seconds == 1
    assert config.defaults.remind_interval_minutes == 10
    assert [t.name for t in config.targets] == ["Frontend", "Backend API"]
    assert config.targets[1].url == "https://api.example.com/health"


def test_load_config_defaults_are_optional(tmp_path):
    config = load_config(write_yaml(tmp_path, MINIMAL_YAML))

    assert config.defaults.check_interval_seconds == 60
    assert config.defaults.timeout_seconds == 10
    assert config.defaults.max_attempts == 3
    assert config.defaults.retry_delay_seconds == 5
    assert config.defaults.remind_interval_minutes == 30


def test_load_config_rejects_empty_targets(tmp_path):
    with pytest.raises(ValueError):
        load_config(write_yaml(tmp_path, "targets: []\n"))


def test_load_config_rejects_non_http_url(tmp_path):
    bad = """\
targets:
  - name: Broken
    url: ftp://example.com
"""
    with pytest.raises(ValueError):
        load_config(write_yaml(tmp_path, bad))


def test_load_config_rejects_duplicate_target_names(tmp_path):
    dup = """\
targets:
  - name: Frontend
    url: https://a.example.com
  - name: Frontend
    url: https://b.example.com
"""
    with pytest.raises(ValueError, match="Frontend"):
        load_config(write_yaml(tmp_path, dup))


@pytest.mark.parametrize(
    "overrides",
    [
        "defaults: {max_attempts: 0}",
        "defaults: {check_interval_seconds: 0}",
        "defaults: {timeout_seconds: 0}",
        "defaults: {retry_delay_seconds: -1}",
        "defaults: {remind_interval_minutes: 0}",
    ],
)
def test_load_config_rejects_non_positive_defaults(tmp_path, overrides):
    with pytest.raises(ValueError):
        load_config(write_yaml(tmp_path, MINIMAL_YAML + overrides + "\n"))


def test_load_slack_settings_reads_env():
    env = {"SLACK_BOT_TOKEN": "xoxb-123", "SLACK_CHANNEL_ID": "C0ABC"}

    settings = load_slack_settings(env)

    assert isinstance(settings, SlackSettings)
    assert settings.bot_token == "xoxb-123"
    assert settings.channel_id == "C0ABC"


@pytest.mark.parametrize("env", [{}, {"SLACK_BOT_TOKEN": "xoxb-123"}, {"SLACK_CHANNEL_ID": "C0ABC"}])
def test_load_slack_settings_requires_both_vars(env):
    with pytest.raises(ValueError):
        load_slack_settings(env)
