# deploy.ps1: puts the ingest service (main.py) on Google Cloud Run. Windows PowerShell.
#
# WHAT THIS FILE DOES
#   Runs the one-time Google Cloud setup and the deploy in order:
#     1. turns on the Google services we use (Cloud Run, Firestore, Secret Manager, Cloud Build)
#     2. creates the Firestore database (skipped if it already exists)
#     3. stores the device key(s) in Secret Manager (never in code)
#     4. builds the container from the Dockerfile and deploys it to Cloud Run
#   At the end it prints the URL to put in the watch's stream_config.h.
#
# Before running: install the Google Cloud CLI (https://cloud.google.com/sdk/docs/install),
# run `gcloud auth login`, and create a project with billing turned on (the free tier
# covers this project, but Cloud Run needs a billing account linked).
#
# Usage (from streaming/server):
#   .\deploy.ps1 -Project my-project-id
#   .\deploy.ps1 -Project my-project-id -NewKey      # make a fresh device key (old one stops working)
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [string]$Region = "asia-southeast1",
    [string]$Service = "silverguard-ingest",
    [string]$DeviceId = "watch-01",
    [switch]$NewKey
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== 1. Project + services"
gcloud config set project $Project
gcloud services enable run.googleapis.com firestore.googleapis.com secretmanager.googleapis.com cloudbuild.googleapis.com

Write-Host "== 2. Firestore database ($Region)"
$dbs = gcloud firestore databases list --format="value(name)" 2>$null
if (-not $dbs) { gcloud firestore databases create --location=$Region }
else { Write-Host "   already exists, skipped" }

Write-Host "== 3. Device key in Secret Manager"
$secret = "silverguard-device-keys"
$exists = $true
try { gcloud secrets describe $secret *> $null; if ($LASTEXITCODE -ne 0) { $exists = $false } } catch { $exists = $false }
if (-not $exists -or $NewKey) {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $key = [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value "$DeviceId=$key" -NoNewline -Encoding ascii
    if ($exists) { gcloud secrets versions add $secret --data-file=$tmp }
    else         { gcloud secrets create  $secret --data-file=$tmp }
    Remove-Item $tmp
    Write-Host ""
    Write-Host "   NEW DEVICE KEY for $DeviceId (put it in stream_config.h as STREAM_DEVICE_KEY):"
    Write-Host "   $key" -ForegroundColor Yellow
    Write-Host "   It is not shown again. Read it later with:"
    Write-Host "   gcloud secrets versions access latest --secret=$secret"
} else { Write-Host "   already exists, kept (use -NewKey to replace)" }

Write-Host "== 4. Let Cloud Run read the secret"
$num = gcloud projects describe $Project --format="value(projectNumber)"
gcloud secrets add-iam-policy-binding $secret `
    --member="serviceAccount:$num-compute@developer.gserviceaccount.com" `
    --role="roles/secretmanager.secretAccessor" *> $null

Write-Host "== 5. Build + deploy (takes a few minutes the first time)"
gcloud run deploy $Service --source . --region $Region --allow-unauthenticated `
    --set-secrets "DEVICE_KEYS=${secret}:latest"

$url = gcloud run services describe $Service --region $Region --format="value(status.url)"
Write-Host ""
Write-Host "Done. In stream_config.h set:" -ForegroundColor Green
Write-Host "  #define STREAM_URL `"$url/ingest`""
Write-Host "Test it:  python ..\tools\send_test_event.py --url $url --key <device key> activity moving"
