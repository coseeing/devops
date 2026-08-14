#!/usr/bin/env bash
set -uo pipefail

INSTANCE_ID="${1:?Usage: run-windows-updates.sh <instance-id>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSM_RUN_SCRIPT="${SSM_RUN_SCRIPT:-${SCRIPT_DIR}/ssm-run.sh}"
TRANSIENT_RETRIES="${WINDOWS_UPDATE_TRANSIENT_RETRIES:-10}"
RETRY_DELAY_SECONDS="${WINDOWS_UPDATE_RETRY_DELAY_SECONDS:-60}"
SSM_READY_ATTEMPTS="${WINDOWS_UPDATE_SSM_READY_ATTEMPTS:-60}"
SSM_POLL_SECONDS="${WINDOWS_UPDATE_SSM_POLL_SECONDS:-10}"
: "${GITHUB_ENV:?GITHUB_ENV must point to the GitHub Actions environment file}"

wait_for_ssm_online() {
  local attempt
  local ping_status

  for attempt in $(seq 1 "${SSM_READY_ATTEMPTS}"); do
    ping_status=$(aws ssm describe-instance-information \
      --filters "Key=InstanceIds,Values=${INSTANCE_ID}" \
      --query 'InstanceInformationList[0].PingStatus' \
      --output text 2>/dev/null || true)

    if [[ "${ping_status}" == "Online" ]]; then
      echo "SSM Agent is Online."
      return 0
    fi

    echo "Waiting for SSM Agent to report Online (attempt ${attempt}/${SSM_READY_ATTEMPTS}; status: ${ping_status:-Unknown})..."
    if (( attempt < SSM_READY_ATTEMPTS )); then
      sleep "${SSM_POLL_SECONDS}"
    fi
  done

  echo "SSM Agent did not report Online after ${SSM_READY_ATTEMPTS} attempts." >&2
  return 1
}

for pass in $(seq 1 5); do
  transient_retry=0
  while true; do
    set +e
    OUTPUT=$(bash "${SSM_RUN_SCRIPT}" "${INSTANCE_ID}" "${SCRIPT_DIR}/install-updates.ps1" 3600)
    SSM_STATUS=$?
    set -e

    printf '%s\n' "${OUTPUT}"
    if (( SSM_STATUS == 0 )); then
      break
    fi

    if ! grep -q '^TRANSIENT_WINDOWS_UPDATE_ERROR=true$' <<< "${OUTPUT}"; then
      exit "${SSM_STATUS}"
    fi

    if (( transient_retry >= TRANSIENT_RETRIES )); then
      echo "Transient Windows Update retry limit reached for pass ${pass}." >&2
      exit "${SSM_STATUS}"
    fi

    transient_retry=$((transient_retry + 1))
    echo "Transient Windows Update failure; retrying pass ${pass} (retry ${transient_retry}/${TRANSIENT_RETRIES})..."
    sleep "${RETRY_DELAY_SECONDS}"
  done

  if grep -q "No updates found." <<< "${OUTPUT}"; then
    echo "No further updates."
    break
  fi

  if grep -q "REBOOT_REQUIRED=true" <<< "${OUTPUT}"; then
    echo "Rebooting instance for updates (pass ${pass})..."
    aws ec2 reboot-instances --instance-ids "${INSTANCE_ID}"
    sleep 30
    aws ec2 wait instance-status-ok --instance-ids "${INSTANCE_ID}"
    wait_for_ssm_online
  fi
done

echo "WINDOWS_UPDATE_DATE=$(date -u +%Y-%m-%d)" >> "${GITHUB_ENV}"
