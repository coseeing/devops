# OpenVPN Security Fix Round 2 Report

## Commit

- Base round-2 commit: `42d6f0c29e3b5dc125cb391303c0acee6fbe1954`
  (`fix: complete OpenVPN forwarding safeguards`).
- Follow-up commit: reported after commit creation in the final delivery. This
  report is part of that same commit, so embedding its own cryptographic object
  ID would change the ID.

## Changes

- The deploy workflow's non-secret remote check now verifies the `nf_tables`
  backend, the exact `FORWARD` to `DOCKER-USER` path, one uniquely commented
  active jump to `OPENVPN-COURSE-A` or `OPENVPN-COURSE-B`, the active chain,
  `inet openvpn_course_input`, and `ip openvpn_course_nat`. It no longer
  checks the removed `inet openvpn_course` table.
- Firewall preflight fails before NAT, IPv4 forwarding, or OpenVPN changes
  when `FORWARD` does not jump to Docker's `DOCKER-USER` chain. The scoped
  helper atomically replaces, removes, and restores the input/NAT tables while
  cleaning up only the legacy course table.
- `inet openvpn_course_input` terminally drops all `tun-course` host INPUT
  traffic; forwarding remains restricted to the existing exact TCP 3389 rule.
- `share` signs first and then calculates the returned-clock plus 600 second
  expiration bound, which is a true upper bound; the URL remains printed once.
- Existing AWS CLI candidates are now version-checked against exact v2
  `2.27.41`. Missing, failed, v1, or mismatched candidates use the existing
  architecture-mapped, PGP-verified installer and receive a post-install
  version verification before `/usr/local/bin/aws` is selected.
- The runbook now distinguishes credential-free static validation from guarded
  deploy-time AWS discovery/input validation, and documents both the Docker
  prerequisite and host-input isolation.

## Base Round Verification

- `uv run pytest` in `vms_portal`: 58 passed.
- `uv run pytest ../tests/openvpn -q` in `vms_portal`: 87 passed, 1 skipped.
- `uv run ansible-playbook --syntax-check ... openvpn-server-playbook.yml`:
  passed.
- Strict `uvx cfn-lint -t` across all repository CloudFormation templates:
  exit 4 solely for the pre-existing W2531 `python3.9` deprecation warning in
  `common-ec2-attach-volume-template.yml`.
- Strict `uvx cfn-lint -t cloudformation/openvpn-distribution-template.yml`:
  passed. The full `-t` run also passed when only that existing W2531 warning
  was explicitly excluded.
- `uvx --from actionlint-py actionlint .github/workflows/deploy-openvpn.yml`:
  passed.
- `bash -n` for all OpenVPN shell scripts, Python compilation for the input
  validator/filter plugin, and `git diff --check`: passed.

## Limitations

- No AWS deployment/resource query, EC2, Docker, Traefik, macOS OpenVPN, or
  Windows RDP test was run. Local verification does not establish production
  behavior.
- The optional namespace integration test remains skipped unless run as root
  with `OPENVPN_RUN_NETNS_TEST=1`, `iproute2`, nftables, and iptables-nft. It
  now models `FORWARD -> DOCKER-USER -> DROP` and verifies helper preflight,
  but it does not send packets; it is a structural integration check only.
- The pinned AWS CLI Team signer material is documented as expiring on
  2027-07-01. Update the pinned key/version before that date or if AWS rotates
  its signer.

## Follow-up Re-review Fixes

- Rotation invokes the real profile renderer with process-scoped `PKI_DIR`,
  `ENDPOINT`, `OPENVPN_PORT`, and `OPENVPN_PROTOCOL` values. The variables are
  not globally exported, and a regression test replaces the mock with the
  installed renderer against realistic PKI material before asserting a TCP/443
  rotated profile.
- Both the local preflight and non-secret workflow post-check require the first
  appended `FORWARD` rule to be exactly `-A FORWARD -j DOCKER-USER`. A prior
  `DROP` or any other rule fails closed before nft/NAT changes. The optional
  namespace check now demonstrates rejected bad ordering before rebuilding the
  valid `FORWARD -> DOCKER-USER -> DROP` structure.
- The unavailable-`DOCKER-USER` teardown warning now accurately states that
  the course input, NAT, and legacy tables are attempted.

## Follow-up Verification

- Focused renderer/rotation, firewall, and workflow regressions: 6 passed.
- `uv run pytest` in `vms_portal`: 58 passed.
- `uv run pytest ../tests/openvpn -q` in `vms_portal`: 91 passed, 1 skipped.
- `uv run ansible-playbook --syntax-check --inventory localhost,
  ../ansible_yaml/openvpn-server-playbook.yml`: passed.
- `uvx --from actionlint-py actionlint .github/workflows/deploy-openvpn.yml`:
  passed.
- `uvx cfn-lint -t cloudformation/openvpn-distribution-template.yml`: passed.
  The full strict CloudFormation run exits 4 solely because of the pre-existing
  W2531 Python 3.9 deprecation warning in
  `cloudformation/common-ec2-attach-volume-template.yml`; it passes with only
  W2531 excluded.
- `bash -n` on all repository shell scripts, Python compilation for the
  OpenVPN/Ansible/Portal Python sources and tests, and `git diff --check`:
  passed.
