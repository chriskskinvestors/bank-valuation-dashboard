<#
  One-shot activator (PowerShell) for Gap A (post-deploy live smoke) + Gap D
  (health alerts). Run from PowerShell on your authenticated machine:

    powershell -ExecutionPolicy Bypass -File ops\finish_smoke_and_alerts.ps1

  It will prompt for the dashboard URL if you don't pass -AppUrl. Uses gcloud.cmd
  and gh (both already work in your PowerShell). The agent shell can't run gcloud
  (Workspace reauth), so this is the one operator step.
#>
param(
  [string]$AppUrl = "",
  [string]$IapClientId = "",
  [string]$AlertEmail = "chris@kskinvestors.com"
)
$ErrorActionPreference = "Stop"
$Project = "ace-beanbag-486220-a8"
$Service = "bank-dashboard"
$Repo    = "chriskskinvestors/bank-valuation-dashboard"
$SA      = "github-deployer@$Project.iam.gserviceaccount.com"

if (-not $AppUrl) { $AppUrl = Read-Host "Dashboard URL you open (the IAP one, e.g. https://dashboard.kskinvestors.com)" }

Write-Host ">> Locating the IAP backend service ..." -ForegroundColor Cyan
$Backend = ""
foreach ($be in (gcloud.cmd compute backend-services list --global --project=$Project --format="value(name)")) {
  if (-not $be) { continue }
  $cid = gcloud.cmd compute backend-services describe $be --global --project=$Project --format="value(iap.oauth2ClientId)"
  if ($cid) { $Backend = $be; if (-not $IapClientId) { $IapClientId = $cid }; Write-Host "   backend=$be  client_id=$cid"; break }
}
if (-not $IapClientId) {
  Write-Host "!! Could not auto-discover the IAP client ID. Find it in Console -> Security ->" -ForegroundColor Yellow
  Write-Host "   Identity-Aware Proxy -> your backend -> OAuth config, then re-run with -IapClientId <id>." -ForegroundColor Yellow
  exit 1
}

Write-Host ">> Setting GitHub repo variables + secret ..." -ForegroundColor Cyan
gh variable set LIVE_SMOKE_ENABLED --body "true"        --repo $Repo
gh variable set APP_URL            --body $AppUrl        --repo $Repo
gh secret   set IAP_CLIENT_ID      --body $IapClientId   --repo $Repo

Write-Host ">> Granting $SA OpenID-token minting + IAP access ..." -ForegroundColor Cyan
gcloud.cmd iam service-accounts add-iam-policy-binding $SA --project=$Project `
  --member="serviceAccount:$SA" --role="roles/iam.serviceAccountOpenIdTokenCreator" --quiet
if ($Backend) {
  gcloud.cmd iap web add-iam-policy-binding --project=$Project `
    --resource-type=backend-services --service=$Backend `
    --member="serviceAccount:$SA" --role="roles/iap.httpsResourceAccessor" --quiet
}

Write-Host ">> Creating Cloud Monitoring alert policies (Gap D) ..." -ForegroundColor Cyan
$Channel = gcloud.cmd beta monitoring channels create --project=$Project `
  --display-name="bank-dashboard alerts ($AlertEmail)" --type=email `
  --channel-labels="email_address=$AlertEmail" --format="value(name)"
Write-Host "   channel: $Channel"

$pol5xx = @"
{
  "displayName": "bank-dashboard - 5xx error rate",
  "combiner": "OR",
  "conditions": [{
    "displayName": "5xx responses sustained for 5 min",
    "conditionThreshold": {
      "filter": "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$Service\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\"",
      "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE", "crossSeriesReducer": "REDUCE_SUM"}],
      "comparison": "COMPARISON_GT", "thresholdValue": 0.05, "duration": "300s", "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["$Channel"],
  "alertStrategy": {"autoClose": "1800s"}
}
"@
$polLive = @"
{
  "displayName": "bank-dashboard - no 2xx requests (possible dead revision)",
  "combiner": "OR",
  "conditions": [{
    "displayName": "2xx request rate ~0 for 15 min",
    "conditionThreshold": {
      "filter": "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$Service\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"2xx\"",
      "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE", "crossSeriesReducer": "REDUCE_SUM"}],
      "comparison": "COMPARISON_LT", "thresholdValue": 0.0001, "duration": "900s", "trigger": {"count": 1}
    }
  }],
  "notificationChannels": ["$Channel"],
  "alertStrategy": {"autoClose": "1800s"}
}
"@
$f1 = Join-Path $env:TEMP "bd-5xx.json"; $pol5xx  | Out-File -FilePath $f1 -Encoding utf8
$f2 = Join-Path $env:TEMP "bd-live.json"; $polLive | Out-File -FilePath $f2 -Encoding utf8
gcloud.cmd alpha monitoring policies create --project=$Project --policy-from-file=$f1
gcloud.cmd alpha monitoring policies create --project=$Project --policy-from-file=$f2

Write-Host ">> Triggering a deploy so the smoke job runs ..." -ForegroundColor Cyan
gh workflow run "Deploy to Cloud Run" --repo $Repo

Write-Host ""
Write-Host ">> DONE. Gap A (live smoke) + Gap D (alerts) are wired." -ForegroundColor Green
Write-Host "   Watch it: gh run watch (gh run list --workflow 'Deploy to Cloud Run' --limit 1 --json databaseId --jq '.[0].databaseId')"
Write-Host "   The 'smoke' job should now RUN (not skip) and go green."
