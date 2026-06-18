#!/usr/bin/env bash
# Gap D (production-readiness audit) — deploy health alerting.
#
# Creates a Cloud Monitoring email notification channel + alert policies for the
# bank-dashboard Cloud Run service so a regression (5xx spike, crash-looping
# revision) pages you instead of being found by hand. Idempotent-ish: re-running
# creates duplicate policies, so run once (or delete the old ones first).
#
# Run from an authenticated terminal (you have gcloud auth; CI does not):
#   bash ops/setup_monitoring_alerts.sh you@kskinvestors.com
# On Windows PowerShell use gcloud.cmd — or run this under Git Bash.
set -euo pipefail

PROJECT="ace-beanbag-486220-a8"
SERVICE="bank-dashboard"
EMAIL="${1:?usage: setup_monitoring_alerts.sh <alert-email>}"

echo ">> Creating email notification channel for ${EMAIL} ..."
CHANNEL=$(gcloud beta monitoring channels create \
  --project="${PROJECT}" \
  --display-name="bank-dashboard alerts (${EMAIL})" \
  --type=email \
  --channel-labels="email_address=${EMAIL}" \
  --format='value(name)')
echo "   channel: ${CHANNEL}"

# ── Policy 1: 5xx error rate ────────────────────────────────────────────────
echo ">> Creating 5xx error-rate alert policy ..."
cat >/tmp/bd-5xx-policy.json <<JSON
{
  "displayName": "bank-dashboard — 5xx error rate",
  "combiner": "OR",
  "conditions": [{
    "displayName": "5xx responses sustained for 5 min",
    "conditionThreshold": {
      "filter": "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SERVICE}\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\"",
      "aggregations": [{
        "alignmentPeriod": "300s",
        "perSeriesAligner": "ALIGN_RATE",
        "crossSeriesReducer": "REDUCE_SUM"
      }],
      "comparison": "COMPARISON_GT",
      "thresholdValue": 0.05,
      "duration": "300s",
      "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["${CHANNEL}"],
  "alertStrategy": {"autoClose": "1800s"}
}
JSON
gcloud alpha monitoring policies create --project="${PROJECT}" --policy-from-file=/tmp/bd-5xx-policy.json

# ── Policy 2 (REMOVED 2026-06-18) — "no 2xx requests" liveness ──────────────
# This keyed on run.googleapis.com/request_count 2xx dropping to ~0 for 15 min as
# a "dead revision" proxy. It FALSE-FIRED on a quiet evening (1:34 AM UTC /
# ~9:34 PM ET): the dashboard is IAP-gated and internal-only, so with no users
# active it legitimately serves ZERO requests — request count can't tell "dead"
# from "idle". min-instances=1 keeps the instance WARM but warm-idle still emits
# 0 2xx. The real failure modes are already covered: the 5xx-rate policy above
# catches an erroring/crash-looping revision, the post-deploy live smoke catches
# a revision that won't render, and a broken NEW revision can't take traffic
# (Cloud Run keeps the previous one serving). So this alert added noise, not
# signal, and is intentionally not recreated. To delete the one already live:
#   gcloud alpha monitoring policies list --project=${PROJECT} \
#     --filter='displayName:"no 2xx requests"' --format='value(name)' \
#     | xargs -r -n1 gcloud alpha monitoring policies delete --quiet --project=${PROJECT}

echo ">> Done. 5xx-rate alert policy created, routed to ${EMAIL}."
echo "   Review/tune them at: https://console.cloud.google.com/monitoring/alerting/policies?project=${PROJECT}"
