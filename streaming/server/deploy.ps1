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
# gcloud prints normal progress messages on the ERROR stream. Windows PowerShell 5.1 shows those
# as red "NativeCommandError" records and, with "Stop", would quit on a success message. So:
# don't stop on messages; judge every gcloud call by its exit code instead (Step below).
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

# Use gcloud.cmd, not the gcloud.ps1 wrapper (the wrapper turns progress messages into errors).
$G = (Get-Command gcloud.cmd -ErrorAction SilentlyContinue).Source
if (-not $G) { $G = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd" }
if (-not (Test-Path $G)) {
    Write-Host "gcloud not found. Install the Google Cloud CLI, then reopen VS Code." -ForegroundColor Red
    exit 1
}

function Step([string]$what) {
    # call right after a gcloud command: stop the script only if it really failed
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $what (gcloud exit code $LASTEXITCODE). Read the message above." -ForegroundColor Red
        exit 1
    }
}

Write-Host "== 1. Project + services"
& $G config set project $Project;                                   Step "set project"
& $G services enable run.googleapis.com firestore.googleapis.com secretmanager.googleapis.com `
    cloudbuild.googleapis.com artifactregistry.googleapis.com;     Step "enable services (is billing linked?)"

Write-Host "== 2. Firestore database ($Region)"
$dbs = & $G firestore databases list --format="value(name)";        Step "list Firestore databases"
if (-not $dbs) { & $G firestore databases create --location=$Region; Step "create Firestore database" }
else { Write-Host "   already exists, skipped" }

Write-Host "== 3. Device key in Secret Manager"
$secret = "silverguard-device-keys"
$found = & $G secrets list --filter="name~/$secret`$" --format="value(name)"; Step "list secrets"
$exists = [bool]$found
if (-not $exists -or $NewKey) {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $key = [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value "$DeviceId=$key" -NoNewline -Encoding ascii
    if ($exists) { & $G secrets versions add $secret --data-file=$tmp }
    else         { & $G secrets create  $secret --data-file=$tmp }
    $code = $LASTEXITCODE
    Remove-Item $tmp
    $global:LASTEXITCODE = $code; Step "store device key"
    Write-Host ""
    Write-Host "   NEW DEVICE KEY for $DeviceId (put it in stream_config.h as STREAM_DEVICE_KEY):"
    Write-Host "   $key" -ForegroundColor Yellow
    Write-Host "   It is not shown again. Read it later with:"
    Write-Host "   gcloud secrets versions access latest --secret=$secret"
} else { Write-Host "   already exists, kept (use -NewKey to replace)" }

Write-Host "== 4. Let Cloud Run read the secret"
$num = & $G projects describe $Project --format="value(projectNumber)"; Step "read project number"
& $G secrets add-iam-policy-binding $secret `
    --member="serviceAccount:$num-compute@developer.gserviceaccount.com" `
    --role="roles/secretmanager.secretAccessor" --condition=None | Out-Null
Step "give Cloud Run access to the secret"

Write-Host "== 5. Build + deploy (takes a few minutes the first time; answer y if asked)"
& $G run deploy $Service --source . --region $Region --allow-unauthenticated `
    --set-secrets "DEVICE_KEYS=${secret}:latest";                  Step "deploy to Cloud Run"

$url = & $G run services describe $Service --region $Region --format="value(status.url)"
Write-Host ""
Write-Host "Done. Service URL: $url" -ForegroundColor Green
Write-Host "In stream_config.h set:"
Write-Host "  #define STREAM_URL `"$url/ingest`""
Write-Host "Device key:  gcloud secrets versions access latest --secret=$secret   (the part after '$DeviceId=')"
Write-Host "Test it (from the project root, put the real key, no brackets):"
Write-Host "  .venv\Scripts\python streaming/tools/send_test_event.py --url $url --key YOUR-KEY activity moving"
