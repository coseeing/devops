from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_sso_health_targets_registered() -> None:
    config = yaml.safe_load((ROOT / "web_health_check/targets.yaml").read_text())
    targets = {target["name"]: target["url"] for target in config["targets"]}
    assert targets["SSO Kratos"] == (
        "https://sso.coseeing.org/kratos/health/ready"
    )
    assert targets["SSO Hydra"] == (
        "https://sso.coseeing.org/.well-known/openid-configuration"
    )
