#!/usr/bin/env bash
set -euo pipefail

# Sends a local PowerShell script to an EC2 instance via SSM Run Command and waits for completion.
# Usage: ssm-run.sh <instance-id> <script-path> [timeout-seconds]

INSTANCE_ID="${1:?Usage: ssm-run.sh <instance-id> <script-path> [timeout-seconds]}"
SCRIPT_PATH="${2:?Usage: ssm-run.sh <instance-id> <script-path> [timeout-seconds]}"
TIMEOUT_SECONDS="${3:-1800}"

SCRIPT_CONTENT=$(cat "${SCRIPT_PATH}")

COMMAND_ID=$(aws ssm send-command \
  --instance-ids "${INSTANCE_ID}" \
  --document-name "AWS-RunPowerShellScript" \
  --parameters "{\"commands\":$(jq -Rs '[.]' <<< "${SCRIPT_CONTENT}"),\"executionTimeout\":[\"${TIMEOUT_SECONDS}\"]}" \
  --query 'Command.CommandId' \
  --output text)

echo "Sent SSM command ${COMMAND_ID} for ${SCRIPT_PATH}" >&2

ELAPSED=0
POLL_INTERVAL=10
STATUS="Pending"
while (( ELAPSED < TIMEOUT_SECONDS + 60 )); do
  STATUS=$(aws ssm get-command-invocation \
    --command-id "${COMMAND_ID}" \
    --instance-id "${INSTANCE_ID}" \
    --query 'Status' \
    --output text 2>/dev/null || echo "Pending")

  case "${STATUS}" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac

  sleep "${POLL_INTERVAL}"
  ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

STDOUT_CONTENT=$(aws ssm get-command-invocation \
  --command-id "${COMMAND_ID}" \
  --instance-id "${INSTANCE_ID}" \
  --query 'StandardOutputContent' \
  --output text)

echo "${STDOUT_CONTENT}"

if [[ "${STATUS}" != "Success" ]]; then
  STDERR_CONTENT=$(aws ssm get-command-invocation \
    --command-id "${COMMAND_ID}" \
    --instance-id "${INSTANCE_ID}" \
    --query 'StandardErrorContent' \
    --output text)
  echo "SSM command ${COMMAND_ID} ended with status ${STATUS}" >&2
  echo "${STDERR_CONTENT}" >&2
  exit 1
fi
