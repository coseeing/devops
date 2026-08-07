#!/usr/bin/env bash
set -uo pipefail

INSTANCE_ID="${1:?Usage: run-windows-updates.sh <instance-id>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSM_RUN_SCRIPT="${SSM_RUN_SCRIPT:-${SCRIPT_DIR}/ssm-run.sh}"
: "${GITHUB_ENV:?GITHUB_ENV must point to the GitHub Actions environment file}"

for pass in $(seq 1 5); do
  set +e
  OUTPUT=$(bash "${SSM_RUN_SCRIPT}" "${INSTANCE_ID}" "${SCRIPT_DIR}/install-updates.ps1" 3600)
  SSM_STATUS=$?
  set -e

  printf '%s\n' "${OUTPUT}"
  if (( SSM_STATUS != 0 )); then
    exit "${SSM_STATUS}"
  fi

  if grep -q "No updates found." <<< "${OUTPUT}"; then
    echo "No further updates."
    break
  fi

  if grep -q "REBOOT_REQUIRED=true" <<< "${OUTPUT}"; then
    echo "Rebooting instance for updates (pass ${pass})..."
    aws ec2 reboot-instances --instance-ids "${INSTANCE_ID}"
    sleep 30
    aws ec2 wait instance-status-ok --instance-ids "${INSTANCE_ID}"
  fi
done

echo "WINDOWS_UPDATE_DATE=$(date -u +%Y-%m-%d)" >> "${GITHUB_ENV}"
