#!/usr/bin/env bash
# One-shot activator for Gap A (post-deploy live smoke) + Gap D (health alerts).
# These need gcloud + gh on YOUR authenticated machine — the CI/agent shell can't
# mint gcloud tokens (Workspace reauth policy). Everything else (the smoke script,
# the gated CI job, the alert-policy authoring) is already committed; this just
# wires the cloud side and flips the smoke on.
#
# Run from Git Bash (so gcloud.cmd + gh are on PATH), once:
#   bash ops/finish_smoke_and_alerts.sh https://<your-dashboard-URL> [IAP_CLIENT_ID]
#
# Arg 1 (required): the URL you open the dashboard at (the IAP-fronted one).
# Arg 2 (optional): the IAP OAuth client ID — auto-discovered if omitted.
set -euo pipefail

PROJECT="ace-beanbag-486220-a8"
REGION="us-central1"
REPO="chriskskinvestors/bank-valuation-dashboard"
SA="github-deployer@${PROJECT}.iam.gserviceaccount.com"
ALERT_EMAIL="${ALERT_EMAIL:-chris@kskinvestors.com}"
APP_URL="${1:?usage: finish_smoke_and_alerts.sh <APP_URL> [IAP_CLIENT_ID]}"
IAP_CLIENT_ID="${2:-}"
GC="gcloud.cmd"; command -v "$GC" >/dev/null 2>&1 || GC="gcloud"

# ── 1. Find the IAP-enabled backend service + its OAuth client ID ────────────
echo ">> Locating the IAP backend service ..."
BACKEND=""
for be in $("$GC" compute backend-services list --global --project="$PROJECT" --format='value(name)' 2>/dev/null); do
  cid=$("$GC" compute backend-services describe "$be" --global --project="$PROJECT" \
        --format='value(iap.oauth2ClientId)' 2>/dev/null || true)
  if [[ -n "$cid" ]]; then
    BACKEND="$be"; [[ -z "$IAP_CLIENT_ID" ]] && IAP_CLIENT_ID="$cid"
    echo "   backend=$be  client_id=$cid"; break
  fi
done
if [[ -z "$IAP_CLIENT_ID" ]]; then
  echo "!! Could not auto-discover the IAP client ID. Find it in Console → Security →"
  echo "   Identity-Aware Proxy → your backend → (3-dot) OAuth config, and pass it as arg 2."
  exit 1
fi

# ── 2. GitHub repo variables + secret (turns the smoke job on) ───────────────
echo ">> Setting GitHub repo variables + secret on ${REPO} ..."
gh variable set LIVE_SMOKE_ENABLED --body "true"      --repo "$REPO"
gh variable set APP_URL            --body "$APP_URL"  --repo "$REPO"
gh secret   set IAP_CLIENT_ID      --body "$IAP_CLIENT_ID" --repo "$REPO"

# ── 3. IAM so github-deployer can mint an IAP token AND pass IAP ─────────────
echo ">> Granting ${SA} OpenID-token minting + IAP access ..."
"$GC" iam service-accounts add-iam-policy-binding "$SA" --project="$PROJECT" \
  --member="serviceAccount:${SA}" --role="roles/iam.serviceAccountOpenIdTokenCreator" --quiet
if [[ -n "$BACKEND" ]]; then
  "$GC" iap web add-iam-policy-binding --project="$PROJECT" \
    --resource-type=backend-services --service="$BACKEND" \
    --member="serviceAccount:${SA}" --role="roles/iap.httpsResourceAccessor" --quiet
else
  echo "   (no backend found to bind IAP accessor — add roles/iap.httpsResourceAccessor"
  echo "    to ${SA} on the IAP resource in the Console.)"
fi

# ── 4. Gap D — health alert policies ────────────────────────────────────────
echo ">> Creating monitoring alert policies (Gap D) ..."
bash "$(dirname "$0")/setup_monitoring_alerts.sh" "$ALERT_EMAIL"

# ── 5. Kick a deploy so the smoke job runs and proves itself ────────────────
echo ">> Triggering a deploy to exercise the smoke job ..."
gh workflow run "Deploy to Cloud Run" --repo "$REPO" || \
  echo "   (start one manually: push to main, or re-run the latest deploy)"

echo ""
echo ">> DONE. Watch the run: gh run watch \$(gh run list --workflow 'Deploy to Cloud Run' --limit 1 --json databaseId --jq '.[0].databaseId')"
echo "   The 'smoke' job should now run (not skip) and go GREEN. If it fails, it caught a real render problem."
