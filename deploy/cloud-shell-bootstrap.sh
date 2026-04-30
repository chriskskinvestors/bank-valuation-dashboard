#!/usr/bin/env bash
#
# Bank-Valuation-Dashboard — Phase 1 GCP bootstrap.
#
# Run this ONCE in Cloud Shell (https://console.cloud.google.com/) to
# provision every resource the dashboard needs.
#
# In Cloud Shell, first clone the repo and cd into it:
#   git clone https://github.com/chriskskinvestors/bank-valuation-dashboard.git
#   cd bank-valuation-dashboard
#   bash deploy/cloud-shell-bootstrap.sh
#
# This script provisions:
#   • APIs enabled
#   • Cloud SQL Postgres instance + database + user
#   • GCS bucket for blob storage
#   • Service account with appropriate roles
#   • Secret Manager entries for API keys (interactive prompts)
#   • Artifact Registry repo for container images
#
# After this completes, run cloud-shell-deploy.sh to build and deploy
# the Cloud Run service.
#
# Idempotent: re-running won't break anything; existing resources are
# kept and skipped with a "(already exists)" note.

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
#  CONFIG — edit these to match your environment
# ──────────────────────────────────────────────────────────────────────
PROJECT_ID="${PROJECT_ID:-ace-beanbag-486220-a8}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-us-central1-a}"

# Cloud SQL
SQL_INSTANCE="${SQL_INSTANCE:-bank-dashboard-db}"
SQL_TIER="${SQL_TIER:-db-custom-1-3840}"   # 1 vCPU, 3.75 GB — smallest practical
SQL_DB_NAME="${SQL_DB_NAME:-dashboard}"
SQL_USER="${SQL_USER:-dashboard}"

# GCS
GCS_BUCKET="${GCS_BUCKET:-ksk-bank-dashboard-data}"

# Service account
SA_NAME="${SA_NAME:-bank-dashboard-sa}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Artifact Registry
AR_REPO="${AR_REPO:-bank-dashboard}"

# Cloud Run
RUN_SERVICE="${RUN_SERVICE:-bank-dashboard}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Bank Dashboard — Phase 1 bootstrap"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Project:    ${PROJECT_ID}"
echo "  Region:     ${REGION}"
echo "  SQL inst:   ${SQL_INSTANCE} (${SQL_TIER})"
echo "  GCS bucket: ${GCS_BUCKET}"
echo "  SA email:   ${SA_EMAIL}"
echo
read -rp "Proceed? [y/N] " confirm
[[ "${confirm,,}" == "y" ]] || { echo "Aborted."; exit 1; }

gcloud config set project "${PROJECT_ID}"

# ──────────────────────────────────────────────────────────────────────
#  1. Enable APIs
# ──────────────────────────────────────────────────────────────────────
echo
echo "▶ Enabling required APIs (this can take 1-2 minutes)..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    sqladmin.googleapis.com \
    secretmanager.googleapis.com \
    storage.googleapis.com \
    cloudscheduler.googleapis.com \
    iap.googleapis.com \
    iam.googleapis.com \
    --quiet

# ──────────────────────────────────────────────────────────────────────
#  2. Service account
# ──────────────────────────────────────────────────────────────────────
echo
echo "▶ Creating service account ${SA_EMAIL}..."
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --quiet >/dev/null 2>&1; then
    gcloud iam service-accounts create "${SA_NAME}" \
        --display-name="Bank Dashboard runtime SA" \
        --description="Used by Cloud Run service and scheduled refresh jobs"
else
    echo "  (already exists)"
fi

for ROLE in \
    roles/cloudsql.client \
    roles/storage.objectAdmin \
    roles/secretmanager.secretAccessor \
    roles/run.invoker
do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${ROLE}" \
        --condition=None \
        --quiet >/dev/null
done

# ──────────────────────────────────────────────────────────────────────
#  3. Cloud SQL Postgres instance
# ──────────────────────────────────────────────────────────────────────
echo
echo "▶ Creating Cloud SQL Postgres instance ${SQL_INSTANCE}..."
echo "  (this takes ~5 minutes the first time)"
if ! gcloud sql instances describe "${SQL_INSTANCE}" --quiet >/dev/null 2>&1; then
    gcloud sql instances create "${SQL_INSTANCE}" \
        --database-version=POSTGRES_15 \
        --tier="${SQL_TIER}" \
        --region="${REGION}" \
        --storage-size=10GB \
        --storage-type=SSD \
        --backup \
        --backup-start-time=08:00 \
        --quiet
else
    echo "  (already exists)"
fi

echo "▶ Creating database ${SQL_DB_NAME}..."
if ! gcloud sql databases describe "${SQL_DB_NAME}" \
        --instance="${SQL_INSTANCE}" --quiet >/dev/null 2>&1; then
    gcloud sql databases create "${SQL_DB_NAME}" \
        --instance="${SQL_INSTANCE}" \
        --quiet
else
    echo "  (already exists)"
fi

echo "▶ Creating database user ${SQL_USER}..."
if ! gcloud sql users list --instance="${SQL_INSTANCE}" \
        --filter="name:${SQL_USER}" --quiet 2>/dev/null | grep -q "${SQL_USER}"; then
    SQL_PASSWORD=$(openssl rand -base64 24 | tr -d "/+=" | head -c 24)
    gcloud sql users create "${SQL_USER}" \
        --instance="${SQL_INSTANCE}" \
        --password="${SQL_PASSWORD}" \
        --quiet

    # Stash the password in Secret Manager immediately
    echo -n "${SQL_PASSWORD}" | gcloud secrets create db-password \
        --replication-policy="automatic" \
        --data-file=- \
        --quiet || echo "${SQL_PASSWORD}" | gcloud secrets versions add db-password --data-file=-
    echo "  Password stored in Secret Manager as 'db-password'"
else
    echo "  (already exists — password already in Secret Manager)"
fi

# ──────────────────────────────────────────────────────────────────────
#  4. GCS bucket
# ──────────────────────────────────────────────────────────────────────
echo
echo "▶ Creating GCS bucket ${GCS_BUCKET}..."
if ! gcloud storage buckets describe "gs://${GCS_BUCKET}" --quiet >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${GCS_BUCKET}" \
        --location="${REGION}" \
        --uniform-bucket-level-access \
        --quiet
else
    echo "  (already exists)"
fi

# ──────────────────────────────────────────────────────────────────────
#  5. Artifact Registry
# ──────────────────────────────────────────────────────────────────────
echo
echo "▶ Creating Artifact Registry repo ${AR_REPO}..."
if ! gcloud artifacts repositories describe "${AR_REPO}" \
        --location="${REGION}" --quiet >/dev/null 2>&1; then
    gcloud artifacts repositories create "${AR_REPO}" \
        --repository-format=docker \
        --location="${REGION}" \
        --description="Container images for the bank dashboard" \
        --quiet
else
    echo "  (already exists)"
fi

# ──────────────────────────────────────────────────────────────────────
#  6. Secret Manager — interactive prompts for API keys
# ──────────────────────────────────────────────────────────────────────
create_secret() {
    local name="$1"
    local prompt="$2"
    if gcloud secrets describe "${name}" --quiet >/dev/null 2>&1; then
        echo "  ${name}: (already exists, skipping)"
        return
    fi
    read -rsp "  ${prompt}: " val
    echo
    if [[ -z "${val}" ]]; then
        echo "  Skipped ${name} (empty input)"
        return
    fi
    echo -n "${val}" | gcloud secrets create "${name}" \
        --replication-policy="automatic" \
        --data-file=- --quiet
    echo "  ${name}: created"
}

echo
echo "▶ Storing API keys in Secret Manager..."
echo "  (Press Enter to skip any you don't have yet — you can add later)"
create_secret "anthropic-api-key" "Anthropic API key (sk-ant-...)"
create_secret "fred-api-key" "FRED API key"

# ──────────────────────────────────────────────────────────────────────
#  Done
# ──────────────────────────────────────────────────────────────────────
INSTANCE_CONN=$(gcloud sql instances describe "${SQL_INSTANCE}" \
    --format="value(connectionName)")

cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Phase 1 bootstrap complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Next:
  1. Note the Cloud SQL connection name (used in deploy step):
       ${INSTANCE_CONN}

  2. Run the deploy script to build and ship to Cloud Run:
       bash deploy/cloud-shell-deploy.sh

  3. After first deploy, enable IAP on the Cloud Run service via:
       Console → Cloud Run → ${RUN_SERVICE} → Security tab → Enable IAP
       Then add KSK members via IAM → IAP-secured Web App User role.

Resources created:
  • Service account:       ${SA_EMAIL}
  • Cloud SQL instance:    ${SQL_INSTANCE} (${SQL_TIER})
  • Cloud SQL database:    ${SQL_DB_NAME}
  • GCS bucket:            gs://${GCS_BUCKET}
  • Artifact Registry:     ${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}
  • Secrets:               db-password, anthropic-api-key, fred-api-key

Estimated monthly cost:   \$30-50 (idle) / \$60-100 (active use)
EOF
