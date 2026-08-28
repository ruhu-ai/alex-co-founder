#!/usr/bin/env bash
# Configure recoverability controls. Dry-run unless --apply is supplied.
set -euo pipefail

APPLY=0
PROJECT=""
REGION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --project) PROJECT="${2:?missing project}"; shift ;;
    --region) REGION="${2:?missing region}"; shift ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done
[[ -n "$PROJECT" && -n "$REGION" ]] \
  || { echo "usage: $0 --project PROJECT --region REGION [--apply]"; exit 2; }

BUCKET="gs://${PROJECT}-artifacts"
commands=(
  "gcloud firestore databases update --project=$PROJECT --database=(default) --enable-pitr --delete-protection"
  "gcloud sql instances patch co-founder-sessions --project=$PROJECT --backup-start-time=03:00 --retained-backups-count=7 --enable-point-in-time-recovery --retained-transaction-log-days=7 --deletion-protection --retain-backups-on-delete --quiet"
  "gcloud storage buckets update $BUCKET --project=$PROJECT --versioning --soft-delete-duration=7d --uniform-bucket-level-access"
)

if [[ "$APPLY" -eq 0 ]]; then
  printf 'DRY RUN — commands that would execute:\n'
  printf '  %s\n' "${commands[@]}"
  exit 0
fi

[[ "${PHASE7_CONFIRM_PROJECT:-}" == "$PROJECT" ]] || {
  echo "refusing apply: set PHASE7_CONFIRM_PROJECT exactly to $PROJECT"
  exit 2
}
gcloud projects describe "$PROJECT" >/dev/null
gcloud firestore databases describe --project="$PROJECT" --database="(default)" >/dev/null
gcloud sql instances describe co-founder-sessions --project="$PROJECT" >/dev/null
gcloud storage buckets describe "$BUCKET" --project="$PROJECT" >/dev/null

gcloud firestore databases update --project="$PROJECT" --database="(default)" \
  --enable-pitr --delete-protection
gcloud sql instances patch co-founder-sessions --project="$PROJECT" \
  --backup-start-time=03:00 --retained-backups-count=7 \
  --enable-point-in-time-recovery --retained-transaction-log-days=7 \
  --deletion-protection --retain-backups-on-delete --quiet
gcloud storage buckets update "$BUCKET" --project="$PROJECT" \
  --versioning --soft-delete-duration=7d --uniform-bucket-level-access

echo "Recoverability controls applied. Run phase7_cloud_audit.py and the"
echo "isolated restore drill in docs/35; configuration alone is not DR proof."
