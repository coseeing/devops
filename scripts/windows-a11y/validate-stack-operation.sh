#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
STACK_SUFFIX="${2:-}"
CONFIRM_STACK_NAME="${3:-}"
INSTANCE_COUNT="${4:-}"
INSTANCE_TYPE="${5:-}"
DISK_SIZE="${6:-}"
STACK_PREFIX="windows-a11y-"
MAX_SUFFIX_LENGTH=115

fail() {
  echo "[stack-operation] $1" >&2
  exit 1
}

if [[ "${ACTION}" != "launch" && "${ACTION}" != "delete" ]]; then
  fail 'Action must be launch or delete.'
fi

if [[ "${STACK_SUFFIX}" == "${STACK_PREFIX}"* ]]; then
  fail "Enter only the suffix, without the ${STACK_PREFIX} prefix."
fi

if (( ${#STACK_SUFFIX} > MAX_SUFFIX_LENGTH )); then
  fail "Stack suffix must be ${MAX_SUFFIX_LENGTH} characters or fewer."
fi

if [[ ! "${STACK_SUFFIX}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
  fail 'Stack suffix must use lowercase letters, numbers, and internal hyphens only.'
fi

STACK_NAME="${STACK_PREFIX}${STACK_SUFFIX}"

if [[ "${ACTION}" == "launch" ]]; then
  if [[ ! "${INSTANCE_COUNT}" =~ ^([1-9]|1[0-9]|20)$ ]]; then
    fail 'Instance count must be an integer from 1 through 20.'
  fi

  if [[ -z "${INSTANCE_TYPE}" ]]; then
    fail 'Instance type is required when action is launch.'
  fi

  if [[ ! "${DISK_SIZE}" =~ ^[1-9][0-9]*$ ]]; then
    fail 'Disk size must be a positive integer when action is launch.'
  fi
fi

if [[ "${ACTION}" == "delete" && "${CONFIRM_STACK_NAME}" != "${STACK_NAME}" ]]; then
  fail "To delete this stack, enter the full stack name exactly: ${STACK_NAME}"
fi

printf '%s\n' "${STACK_NAME}"
