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
        traefik_labels_oathkeeper=["traefik.http.routers.hydra-admin.rule=test"],
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
        "OATHKEEPER_SHARED_SECRET": "oathkeeper-secret",
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
    assert "CENTER_" not in without_old
    assert "OATHKEEPER_SHARED_SECRET=oathkeeper-secret" in without_old

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
    assert "//kratos-admin:" not in rendered
    assert "//hydra-admin:" not in rendered
    center = compose["services"]["center"]["environment"]
    assert center["NEXT_PUBLIC_ORY_SDK_URL"] == "http://sso-kratos-dev:4433"
    assert center["KRATOS_ADMIN_URL"] == "http://sso-kratos-admin-dev:4434"
    assert center["HYDRA_ADMIN_URL"] == "http://sso-hydra-admin-dev:4445"
    assert center["SSO_PUBLIC_ORIGIN"] == "${SSO_PUBLIC_ORIGIN}"
    kratos = compose["services"]["kratos"]["environment"]
    assert kratos["OAUTH2_PROVIDER_URL"] == "http://sso-hydra-admin-dev:4445"


def test_compose_has_no_published_ports_and_has_memory_limits() -> None:
    services = yaml.safe_load(render_compose())["services"]
    assert all("ports" not in service for service in services.values())
    assert services["hydra"]["mem_limit"] == "256M"
    assert services["hydra-admin"]["mem_limit"] == "128M"
    assert services["kratos-admin"]["mem_limit"] == "256M"
    assert services["oathkeeper"]["mem_limit"] == "128M"
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
    assert public == {"hydra", "kratos", "kratos-schemas", "oathkeeper", "center"}
    for admin in ("hydra-admin", "kratos-admin"):
        assert services[admin]["networks"] == ["sso-dev"]
        assert services[admin]["labels"] == ["traefik.enable=false"]


def test_public_and_admin_apis_run_in_separate_containers() -> None:
    services = yaml.safe_load(render_compose())["services"]
    assert services["hydra"]["command"][-1] == "public"
    assert services["hydra-admin"]["command"][-1] == "admin"
    assert services["hydra-admin"]["environment"] == services["hydra"]["environment"]
    assert "/etc/config/kratos/bind-public-only.yml" in services["kratos"]["command"]
    assert "--watch-courier" in services["kratos"]["command"]
    assert "/etc/config/kratos/bind-admin-only.yml" in services["kratos-admin"]["command"]
    assert "--watch-courier" not in services["kratos-admin"]["command"]
    assert services["kratos-admin"]["environment"] == services["kratos"]["environment"]


def test_oauth_sign_in_returns_to_hydra() -> None:
    kratos = yaml.safe_load(render_compose())["services"]["kratos"]["environment"]
    assert kratos["SELFSERVICE_ALLOWED_RETURN_URLS_2"] == "${SSO_PUBLIC_ORIGIN}/oauth2/auth"
    assert kratos["OAUTH2_PROVIDER_OVERRIDE_RETURN_TO"] == "true"


def test_oathkeeper_keeps_go_templates_and_reads_issuer_from_env() -> None:
    oathkeeper = yaml.safe_load(render_compose())["services"]["oathkeeper"]
    env = oathkeeper["environment"]
    headers = json.loads(env["MUTATORS_HEADER_CONFIG_HEADERS"])
    assert headers == {
        "X-Auth-Subject": "{{ .Subject }}",
        "X-Auth-Email": "{{ .Extra.email }}",
        "X-From-Oathkeeper": "${OATHKEEPER_SHARED_SECRET}",
    }
    assert json.loads(env["AUTHENTICATORS_JWT_CONFIG_JWKS_URLS"]) == [
        "${SSO_PUBLIC_ORIGIN}/.well-known/jwks.json"
    ]
    assert json.loads(env["AUTHENTICATORS_JWT_CONFIG_TRUSTED_ISSUERS"]) == [
        "${SSO_PUBLIC_ORIGIN}"
    ]
    assert (
        "traefik.http.services.sso-service-dev--oathkeeper.loadbalancer.server.port=4455"
        in oathkeeper["labels"]
    )


def test_oathkeeper_routes_cover_both_admin_apis() -> None:
    config = yaml.safe_load((ROOT / "ansible_yaml/extra/sso-oathkeeper.yml").read_text())
    routers = config["http"]["routers"]
    assert set(routers) == {"hydra-admin", "kratos-admin"}
    assert "PathPrefix(`/hydra/admin`)" in routers["hydra-admin"]["rule"]
    assert "PathPrefix(`/kratos/admin`)" in routers["kratos-admin"]["rule"]
    # Must beat the /kratos public router (priority 20).
    assert all(router["priority"] == 100 for router in routers.values())


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
        "sso-oathkeeper",
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
