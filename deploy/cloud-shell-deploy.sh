#!/usr/bin/env bash
#
# Build the container image from current code and deploy to Cloud Run.
#
# Run this AFTER cloud-shell-bootstrap.sh has succeeded once.
# Subsequent deploys are fast (~3 min) because Docker layers cache.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-ace-beanbag-486220-a8}"
REGION="${REGION:-us-central1}"
SQL_INSTANCE="${SQL_INSTANCE:-bank-dashboard-db}"
SQL_DB_NAME="${SQL_DB_NAME:-dashboard}"
SQL_USER="${SQL_USER:-dashboard}"
GCS_BUCKET="${GCS_BUCKET:-ksk-bank-dashboard-data}"
SA_NAME="${SA_NAME:-bank-dashboard-sa}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
AR_REPO="${AR_REPO:-bank-dashboard}"
RUN_SERVICE="${RUN_SERVICE:-bank-dashboard}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${RUN_SERVICE}:$(date +%Y%m%d-%H%M%S)"
INSTANCE_CONN=$(gcloud sql instances describe "${SQL_INSTANCE}" --format="value(connectionName)")

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Building and deploying ${RUN_SERVICE}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Image:        ${IMAGE}"
echo "  Cloud SQL:    ${INSTANCE_CONN}"
echo

# Configure docker auth for Artifact Registry (idempotent)
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# Build via Cloud Build (uses local Dockerfile, runs in GCP)
echo "▶ Building image (Cloud Build, ~3-5 min)..."
gcloud builds submit --tag "${IMAGE}" --quiet

# DATABASE_URL via the Cloud SQL Unix socket — no IP, no proxy needed
DB_PASSWORD=$(gcloud secrets versions access latest --secret=db-password)
DATABASE_URL="postgresql+psycopg2://${SQL_USER}:${DB_PASSWORD}@/${SQL_DB_NAME}?host=/cloudsql/${INSTANCE_CONN}"

# Deploy to Cloud Run
echo
echo "▶ Deploying to Cloud Run..."
gcloud run deploy "${RUN_SERVICE}" \
    --image="${IMAGE}" \
    --region="${REGION}" \
    --service-account="${SA_EMAIL}" \
    --add-cloudsql-instances="${INSTANCE_CONN}" \
    --set-env-vars="GCS_BUCKET=${GCS_BUCKET},GCLOUD_PROJECT=${PROJECT_ID},DATABASE_URL=${DATABASE_URL}" \
    --set-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest,FRED_API_KEY=fred-api-key:latest" \
    --memory=2Gi \
    --cpu=1 \
    --min-instances=0 \
    --max-instances=4 \
    --concurrency=20 \
    --timeout=300 \
    --no-allow-unauthenticated \
    --quiet

URL=$(gcloud run services describe "${RUN_SERVICE}" \
        --region="${REGION}" --format="value(status.url)")

cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Deploy complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

URL:  ${URL}

Currently locked down (--no-allow-unauthenticated). To open it up to
KSK Workspace users via IAP:

  1. Console → Cloud Run → ${RUN_SERVICE} → Security tab
  2. Toggle "Cloud IAP" on
  3. IAM page → IAP-secured Web App User → ADD MEMBERS
       Add: domain:kskinvestors.com (or individual emails)

Once IAP is enabled, the URL will redirect through Google login and
only allow ${USER:-your}-domain users.

EOF
