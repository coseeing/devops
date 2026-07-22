#!/usr/bin/env bash
set -euo pipefail

# Sends a local PowerShell script to an EC2 instance via SSM Run Command and waits for completion.
# Usage: ssm-run.sh <instance-id> <script-path> [timeout-seconds]

INSTANCE_ID="${1:?Usage: ssm-run.sh <instance-id> <script-path> [timeout-seconds]}"
SCRIPT_PATH="${2:?Usage: ssm-run.sh <instance-id> <script-path> [timeout-seconds]}"
TIMEOUT_SECONDS="${3:-1800}"
CLOUDWATCH_LOG_GROUP="${SSM_CLOUDWATCH_LOG_GROUP:-/aws/ssm/windows-a11y}"

SCRIPT_CONTENT=$(cat "${SCRIPT_PATH}")

COMMAND_ID=$(aws ssm send-command \
  --instance-ids "${INSTANCE_ID}" \
  --document-name "AWS-RunPowerShellScript" \
  --parameters "{\"commands\":$(jq -Rs '[.]' <<< "${SCRIPT_CONTENT}"),\"executionTimeout\":[\"${TIMEOUT_SECONDS}\"]}" \
  --cloud-watch-output-config "CloudWatchOutputEnabled=true,CloudWatchLogGroupName=${CLOUDWATCH_LOG_GROUP}" \
  --query 'Command.CommandId' \
  --output text)

echo "Sent SSM command ${COMMAND_ID} for ${SCRIPT_PATH}" >&2
echo "Live output: CloudWatch Logs ${CLOUDWATCH_LOG_GROUP}" >&2

CW_STDOUT_TOKEN=""
CW_STDERR_TOKEN=""

fetch_cloudwatch_stream() {
  local stream_suffix="$1"
  local token_variable="$2"
  local stream_name="${COMMAND_ID}/${INSTANCE_ID}/aws:runPowerShellScript/${stream_suffix}"
  local token="${!token_variable}"
  local args=(
    logs get-log-events
    --log-group-name "${CLOUDWATCH_LOG_GROUP}"
    --log-stream-name "${stream_name}"
    --start-from-head
    --output json
  )
  local response
  local next_token

  if [[ -n "${token}" ]]; then
    args+=(--next-token "${token}")
  fi

  response=$(aws "${args[@]}" 2>/dev/null) || return 0
  jq -r '.events[].message' <<< "${response}" >&2
  next_token=$(jq -r '.nextForwardToken // empty' <<< "${response}")
  printf -v "${token_variable}" '%s' "${next_token}"
}

ELAPSED=0
POLL_INTERVAL=10
STATUS="Pending"
STATUS_DETAILS="Waiting for SSM Agent"
EXECUTION_ELAPSED=""
while (( ELAPSED < TIMEOUT_SECONDS + 60 )); do
  INVOCATION=$(aws ssm get-command-invocation \
    --command-id "${COMMAND_ID}" \
    --instance-id "${INSTANCE_ID}" \
    --query '{Status:Status,StatusDetails:StatusDetails,ExecutionElapsedTime:ExecutionElapsedTime}' \
    --output json 2>/dev/null || true)

  if [[ -n "${INVOCATION}" ]]; then
    STATUS=$(jq -r '.Status // "Pending"' <<< "${INVOCATION}")
    STATUS_DETAILS=$(jq -r '.StatusDetails // "Unknown"' <<< "${INVOCATION}")
    EXECUTION_ELAPSED=$(jq -r '.ExecutionElapsedTime // empty' <<< "${INVOCATION}")
  fi

  printf '[ssm-run] local_elapsed=%ss status=%s details=%s remote_elapsed=%s\n' \
    "${ELAPSED}" "${STATUS}" "${STATUS_DETAILS}" "${EXECUTION_ELAPSED:-n/a}" >&2
  fetch_cloudwatch_stream stdout CW_STDOUT_TOKEN
  fetch_cloudwatch_stream stderr CW_STDERR_TOKEN

  case "${STATUS}" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac

  sleep "${POLL_INTERVAL}"
  ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

FINAL_INVOCATION=$(aws ssm get-command-invocation \
  --command-id "${COMMAND_ID}" \
  --instance-id "${INSTANCE_ID}" \
  --output json)

STATUS=$(jq -r '.Status' <<< "${FINAL_INVOCATION}")
STATUS_DETAILS=$(jq -r '.StatusDetails' <<< "${FINAL_INVOCATION}")
EXECUTION_ELAPSED=$(jq -r '.ExecutionElapsedTime // "n/a"' <<< "${FINAL_INVOCATION}")
RESPONSE_CODE=$(jq -r '.ResponseCode' <<< "${FINAL_INVOCATION}")
STDOUT_CONTENT=$(jq -r '.StandardOutputContent // empty' <<< "${FINAL_INVOCATION}")
STDERR_CONTENT=$(jq -r '.StandardErrorContent // empty' <<< "${FINAL_INVOCATION}")

echo "${STDOUT_CONTENT}"

if [[ "${STATUS}" != "Success" ]]; then
  echo "SSM command ${COMMAND_ID} ended with status ${STATUS} (${STATUS_DETAILS}), response code ${RESPONSE_CODE}, elapsed ${EXECUTION_ELAPSED}" >&2
  echo "${STDERR_CONTENT}" >&2
  exit 1
fi
