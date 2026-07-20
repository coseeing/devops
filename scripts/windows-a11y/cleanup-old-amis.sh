#!/usr/bin/env bash
set -euo pipefail

# Keeps the N most recently created AMIs (and their snapshots) matching a Name tag prefix.
# Usage: cleanup-old-amis.sh <name-prefix> <keep-count>

NAME_PREFIX="${1:?Usage: cleanup-old-amis.sh <name-prefix> <keep-count>}"
KEEP_COUNT="${2:?Usage: cleanup-old-amis.sh <name-prefix> <keep-count>}"

mapfile -t AMI_IDS < <(
  aws ec2 describe-images \
    --owners self \
    --filters "Name=name,Values=${NAME_PREFIX}*" \
    --query 'sort_by(Images, &CreationDate)[].ImageId' \
    --output text | tr '\t' '\n'
)

TOTAL=${#AMI_IDS[@]}
echo "Found ${TOTAL} AMI(s) matching prefix '${NAME_PREFIX}', keeping ${KEEP_COUNT} most recent."

if (( TOTAL <= KEEP_COUNT )); then
  echo "Nothing to clean up."
  exit 0
fi

DELETE_COUNT=$((TOTAL - KEEP_COUNT))
for ((i = 0; i < DELETE_COUNT; i++)); do
  AMI_ID="${AMI_IDS[$i]}"

  mapfile -t SNAPSHOT_IDS < <(
    aws ec2 describe-images --image-ids "${AMI_ID}" \
      --query 'Images[0].BlockDeviceMappings[].Ebs.SnapshotId' \
      --output text | tr '\t' '\n'
  )

  echo "Deregistering ${AMI_ID} and deleting ${#SNAPSHOT_IDS[@]} snapshot(s)."
  aws ec2 deregister-image --image-id "${AMI_ID}"

  for SNAPSHOT_ID in "${SNAPSHOT_IDS[@]}"; do
    [[ -n "${SNAPSHOT_ID}" && "${SNAPSHOT_ID}" != "None" ]] || continue
    aws ec2 delete-snapshot --snapshot-id "${SNAPSHOT_ID}"
  done
done
