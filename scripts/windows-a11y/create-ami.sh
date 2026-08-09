#!/usr/bin/env bash
set -euo pipefail

INSTANCE_ID="${1:?Usage: create-ami.sh <instance-id> <ami-name>}"
AMI_NAME="${2:?Usage: create-ami.sh <instance-id> <ami-name>}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must point to the GitHub Actions output file}"

MAX_ATTEMPTS="${AMI_MAX_ATTEMPTS:-120}"
POLL_INTERVAL_SECONDS="${AMI_POLL_INTERVAL_SECONDS:-15}"
LOG_PREFIX="[create-ami]"

IMAGE_ID=$(aws ec2 create-image \
  --instance-id "${INSTANCE_ID}" \
  --name "${AMI_NAME}" \
  --description "Windows Server 2025 A11y test environment - ${AMI_NAME}" \
  --query 'ImageId' \
  --output text)

echo "${LOG_PREFIX} Created AMI ${IMAGE_ID}."
echo "ami_id=${IMAGE_ID}" >> "${GITHUB_OUTPUT}"

for attempt in $(seq 1 "${MAX_ATTEMPTS}"); do
  set +e
  IMAGE_JSON=$(aws ec2 describe-images --image-ids "${IMAGE_ID}" --output json 2>&1)
  DESCRIBE_STATUS=$?
  set -e

  if (( DESCRIBE_STATUS != 0 )); then
    if grep -q 'InvalidAMIID.NotFound' <<< "${IMAGE_JSON}"; then
      echo "${LOG_PREFIX} AMI ${IMAGE_ID} is not visible yet (attempt ${attempt} of ${MAX_ATTEMPTS})."
      if (( attempt < MAX_ATTEMPTS )); then
        sleep "${POLL_INTERVAL_SECONDS}"
      fi
      continue
    fi

    echo "${IMAGE_JSON}" >&2
    exit "${DESCRIBE_STATUS}"
  fi

  STATE=$(jq -r '.Images[0].State // "missing"' <<< "${IMAGE_JSON}")
  STATE_REASON=$(jq -r '.Images[0].StateReason.Message // empty' <<< "${IMAGE_JSON}")

  if [[ -n "${STATE_REASON}" ]]; then
    echo "${LOG_PREFIX} AMI ${IMAGE_ID} state: ${STATE} (attempt ${attempt} of ${MAX_ATTEMPTS}); reason: ${STATE_REASON}."
  else
    echo "${LOG_PREFIX} AMI ${IMAGE_ID} state: ${STATE} (attempt ${attempt} of ${MAX_ATTEMPTS})."
  fi

  case "${STATE}" in
    available)
      exit 0
      ;;
    failed)
      echo "${LOG_PREFIX} AMI ${IMAGE_ID} creation failed." >&2
      exit 1
      ;;
    pending)
      if (( attempt < MAX_ATTEMPTS )); then
        sleep "${POLL_INTERVAL_SECONDS}"
      fi
      ;;
    *)
      echo "${LOG_PREFIX} Unexpected AMI state '${STATE}' for ${IMAGE_ID}." >&2
      exit 1
      ;;
  esac
done

echo "${LOG_PREFIX} Timed out waiting for AMI ${IMAGE_ID} after ${MAX_ATTEMPTS} checks." >&2
exit 1
