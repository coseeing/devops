# OpenVPN Security Fix Round 2 Report

## Commit

- Commit message: `fix: complete OpenVPN forwarding safeguards`
- Commit SHA: reported after commit creation in the final delivery. This report
  is part of that same commit, so embedding its own cryptographic object ID
  would change the ID.

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

## Verification

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
