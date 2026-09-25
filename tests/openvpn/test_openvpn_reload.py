"""Exercise the real handler control flow with a harmless service module."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROLE = Path(__file__).resolve().parents[2] / "ansible_yaml/roles/openvpn_server"


@pytest.mark.parametrize(
    ("reload_error", "restart_error", "rolled_back", "states"),
    [
        ("", "", False, ["reloaded"]),
        ("Job type reload is not applicable for unit openvpn-server@course.service.",
         "", False, ["reloaded", "restarted"]),
        ("reload not supported", "", False, ["reloaded", "restarted"]),
        ("Invalid configuration", "", True, ["reloaded"]),
        ("Job type reload is not applicable", "Restart failed", True,
         ["reloaded", "restarted"]),
    ],
)
def test_reload_handler_fallback_and_rollback(
    tmp_path: Path, reload_error: str, restart_error: str,
    rolled_back: bool, states: list[str],
) -> None:
    role = tmp_path / "roles/openvpn_server"
    shutil.copytree(ROLE, role)
    (role / "tasks/no-op.yml").write_text("[]\n")
    # Keep the shipped handler/block/conditions intact; replace only the remote
    # service boundary, so this test never controls the developer's services.
    reload_file = role / "tasks/reload-openvpn.yml"
    reload_file.write_text(reload_file.read_text().replace(
        "ansible.builtin.systemd_service:", "fake_service:"
    ))
    library = tmp_path / "library"
    library.mkdir()
    (library / "fake_service.py").write_text('''
import os
from ansible.module_utils.basic import AnsibleModule
m = AnsibleModule(argument_spec=dict(name=dict(required=True), state=dict(required=True)))
if m.params['name'] != 'openvpn-server@course':
    m.fail_json(msg='Unexpected service')
state = m.params['state']
with open(os.environ['SERVICE_CALLS'], 'a') as f:
    f.write(state + '\\n')
error = os.environ['RELOAD_ERROR'] if state == 'reloaded' else os.environ['RESTART_ERROR']
if error:
    m.fail_json(msg=error)
m.exit_json(changed=True)
''')
    playbook = tmp_path / "reload.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "gather_facts": False,
        "tasks": [
            {"ansible.builtin.import_role": {"name": str(role), "tasks_from": "no-op"}},
            {"block": [
                {"ansible.builtin.debug": {"msg": "configuration changed"},
                 "changed_when": True, "notify": "Reload course OpenVPN"},
                {"ansible.builtin.meta": "flush_handlers"},
             ], "rescue": [
                {"ansible.builtin.set_fact": {"rolled_back": True}},
             ]},
            {"ansible.builtin.assert": {"that": [
                "(rolled_back | default(false) | bool) == " + str(rolled_back).lower(),
            ]}},
        ],
    }], sort_keys=False))
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook)],
        env={**os.environ, "ANSIBLE_LIBRARY": str(library),
             "ANSIBLE_LOCAL_TEMP": str(tmp_path / "local"),
             "ANSIBLE_REMOTE_TEMP": str(tmp_path / "remote"),
             "ANSIBLE_NOCOLOR": "1", "SERVICE_CALLS": str(calls),
             "RELOAD_ERROR": reload_error, "RESTART_ERROR": restart_error},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text().splitlines() == states
