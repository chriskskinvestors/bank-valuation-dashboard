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

# ── Policy 2: container instance startup / crash signal ─────────────────────
# A crash-looping revision shows up as repeated container starts; alert when the
# startup latency metric stops reporting healthy or instances churn. The 5xx
# policy catches most user-visible breakage; this catches a revision that can't
# even serve. Uses the request latency stream as a liveness proxy (no requests
# being served at all for a sustained window on a min-instances=1 service is
# itself a signal).
echo ">> Creating 'no successful requests' liveness alert policy ..."
cat >/tmp/bd-live-policy.json <<JSON
{
  "displayName": "bank-dashboard — no 2xx requests (possible dead revision)",
  "combiner": "OR",
  "conditions": [{
    "displayName": "2xx request rate dropped to ~0 for 15 min",
    "conditionThreshold": {
      "filter": "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SERVICE}\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"2xx\"",
      "aggregations": [{
        "alignmentPeriod": "300s",
        "perSeriesAligner": "ALIGN_RATE",
        "crossSeriesReducer": "REDUCE_SUM"
      }],
      "comparison": "COMPARISON_LT",
      "thresholdValue": 0.0001,
      "duration": "900s",
      "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["${CHANNEL}"],
  "alertStrategy": {"autoClose": "1800s"}
}
JSON
gcloud alpha monitoring policies create --project="${PROJECT}" --policy-from-file=/tmp/bd-live-policy.json

echo ">> Done. Two alert policies created, routed to ${EMAIL}."
echo "   Review/tune them at: https://console.cloud.google.com/monitoring/alerting/policies?project=${PROJECT}"
