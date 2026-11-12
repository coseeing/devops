from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = ROOT / "ansible_yaml/sso-playbook.yml"


def load_play() -> dict[str, Any]:
    return yaml.safe_load(PLAYBOOK.read_text())[0]


def named_task(name: str) -> dict[str, Any]:
    return next(task for task in load_play()["tasks"] if task["name"] == name)


def render_template(source: str, **context: object) -> str:
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.filters["to_json"] = json.dumps
    return environment.from_string(source).render(**context)


def render_compose(secret_data: dict[str, str] | None = None) -> str:
    task = named_task("Write SSO Compose file")
    return render_template(
        task["copy"]["content"],
        ecr_location="622913514517.dkr.ecr.ap-northeast-1.amazonaws.com",
        deploy_tag="dev",
        deploy_domain="sso.dev.coseeing.org",
        project_postfix="-dev",
        image_name="622913514517.dkr.ecr.ap-northeast-1.amazonaws.com/sso-center:dev",
        secret_data=secret_data or {},
        traefik_router_prefix="sso-service-dev--",
        traefik_labels_list=["traefik.http.routers.center.rule=test"],
        traefik_labels_hydra=["traefik.http.routers.hydra.rule=test"],
        traefik_labels_kratos=["traefik.http.routers.kratos.rule=test"],
        traefik_labels_kratos_schemas=[
            "traefik.http.routers.kratos-schemas.rule=test"
        ],
    )


def test_first_task_asserts_traefik_exists() -> None:
    tasks = load_play()["tasks"]
    assert tasks[0]["name"] == "Check existing Traefik configuration"
    assert tasks[0]["stat"]["path"] == "{{ traefik_path }}/traefik.yml"
    assert tasks[1]["name"] == "Require existing Traefik configuration"
    assertion = tasks[1]["assert"]
    assert assertion["that"] == ["sso_traefik_config.stat.exists"]
    assert "主機上沒有 Traefik" in assertion["fail_msg"]
    assert "entry 網路" in assertion["fail_msg"]


def test_secret_name_is_per_environment() -> None:
    secret_name = load_play()["vars"]["secret_name"]
    assert render_template(secret_name, project_postfix="-dev") == "dev/sso"
    assert render_template(secret_name, project_postfix="") == "prod/sso"


def test_env_file_is_private_and_not_logged() -> None:
    task = named_task("Write SSO environment file")
    assert task["copy"]["mode"] == "0600"
    assert task["no_log"] is True


def test_env_template_omits_missing_old_secrets() -> None:
    content = named_task("Write SSO environment file")["copy"]["content"]
    base_secret = {
        "HYDRA_DSN": "hydra-dsn",
        "KRATOS_DSN": "kratos-dsn",
        "HYDRA_SYSTEM_SECRET": "hydra-secret",
        "KRATOS_COOKIE_SECRET": "cookie-secret",
        "KRATOS_CIPHER_SECRET": "cipher-secret",
        "KRATOS_GOOGLE_CLIENT_ID": "google-id",
        "KRATOS_GOOGLE_CLIENT_SECRET": "google-secret",
        "KRATOS_GMAIL_ADDRESS": "sso@example.com",
        "KRATOS_GMAIL_APP_PASSWORD": "gmail-secret",
        "CENTER_COOKIE_SECRET": "center-cookie",
        "CENTER_CSRF_COOKIE_SECRET": "center-csrf",
        "TRUSTED_CONSENT_CLIENT_IDS": "client-id",
    }
    without_old = render_template(
        content,
        deploy_domain="sso.dev.coseeing.org",
        deploy_tag="dev",
        project_postfix="-dev",
        secret_data=base_secret,
    )
    assert "_OLD=" not in without_old

    with_old = render_template(
        content,
        deploy_domain="sso.dev.coseeing.org",
        deploy_tag="dev",
        project_postfix="-dev",
        secret_data={**base_secret, "HYDRA_SYSTEM_SECRET_OLD": "old-hydra"},
    )
    assert "HYDRA_SYSTEM_SECRET_OLD=old-hydra" in with_old


def test_compose_internal_urls_are_environment_scoped() -> None:
    rendered = render_compose()
    compose = yaml.safe_load(rendered)
    for service in compose["services"].values():
        assert service["container_name"].endswith("-dev")
    assert "//kratos:" not in rendered
    assert "//hydra:" not in rendered
    assert "http://sso-kratos-dev:4433" in rendered
    assert "http://sso-hydra-dev:4445" in rendered


def test_compose_has_no_published_ports_and_has_memory_limits() -> None:
    services = yaml.safe_load(render_compose())["services"]
    assert all("ports" not in service for service in services.values())
    assert services["hydra"]["mem_limit"] == "256M"
    assert services["kratos"]["mem_limit"] == "256M"
    assert services["kratos-schemas"]["mem_limit"] == "32M"
    assert services["center"]["mem_limit"] == "384M"


def test_only_public_services_join_entry() -> None:
    services = yaml.safe_load(render_compose())["services"]
    public = {
        name
        for name, service in services.items()
        if "entry" in service.get("networks", [])
    }
    assert public == {"hydra", "kratos", "kratos-schemas", "center"}


def test_all_sso_images_are_untagged_before_compose_up() -> None:
    play = load_play()
    tasks = play["tasks"]
    names = [task["name"] for task in tasks]
    untag = named_task("Untag other SSO images before deployment")
    assert untag["community.docker.docker_image"] == {
        "name": "{{ ecr_location }}/{{ item }}:{{ deploy_tag }}",
        "state": "absent",
        "force_absent": True,
    }
    assert untag["loop"] == "{{ sso_other_images }}"
    assert play["vars"]["sso_other_images"] == [
        "sso-kratos",
        "sso-hydra",
        "sso-kratos-schemas",
    ]
    assert names.index(untag["name"]) < names.index("Include common deployment tasks")


def test_domain_json_and_ansible_workflow_register_sso() -> None:
    domains = json.loads((ROOT / ".github/workflows/config/domain.json").read_text())
    assert domains["sso"] == {
        "prod": {"domain": "sso.coseeing.org"},
        "dev": {"domain": "sso.dev.coseeing.org"},
    }
    workflow = yaml.safe_load((ROOT / ".github/workflows/ansible.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    options = triggers["workflow_dispatch"]["inputs"]["deploy_type"]["options"]
    assert "sso" in options
