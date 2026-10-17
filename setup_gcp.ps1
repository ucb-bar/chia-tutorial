# CHIA tutorial: one-time GCP setup (Windows version of setup_gcp.sh).
#
# Installs a private copy of the gcloud CLI (your existing gcloud install and
# its config are not touched), signs you in, creates/uses a GCP project with
# billing, enables the APIs the tutorial needs, and starts one VM:
#   chia-dev    c2d-standard-8  (AMD Milan, 8 vCPU / 32 GB) - your dev machine / CHIA head
# with Docker, conda (Miniforge) and the Antigravity CLI (agy), signed in. More machines are
# brought up later by CHIA clusters.
#
# Usage (from PowerShell, in this folder):
#   powershell -ExecutionPolicy Bypass -File .\setup_gcp.ps1 -Account you@gmail.com [-Project ID] [-BillingAccount ID]
#   powershell -ExecutionPolicy Bypass -File .\setup_gcp.ps1 -Account you@gmail.com -Teardown   # delete the VM
#   powershell -ExecutionPolicy Bypass -File .\setup_gcp.ps1 -Account you@gmail.com -Purge      # also offer to delete the project, sign out, remove local files
#
# Sign-in happens in the browser (Google OAuth); the script never sees your password.
# Safe to re-run: existing project, VMs and installs are reused.
# Needs Windows 10 (1809 or later) or Windows 11, for the built-in curl.exe, tar.exe and ssh.exe.
#
# Optional environment overrides:
#   $env:DEV_ZONE = 'us-west4-c'   $env:TUT_HOME = "$HOME\.chia-tutorial"
param(
    [string]$Account,
    [string]$Project = $env:PROJECT_ID,
    [string]$BillingAccount = $env:BILLING_ACCOUNT,
    [switch]$Teardown,
    [switch]$Purge
)
$ErrorActionPreference = 'Stop'

function Show-Usage {
    Get-Content -LiteralPath $PSCommandPath | Where-Object { $_ -like '#   powershell *' } |
        ForEach-Object { 'usage: ' + $_.Substring(4) }
    exit 1
}
if ($args.Count -or -not $Account) { Show-Usage }
$DoTeardown = $Teardown -or $Purge
$Self = "powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""

$TutHome = if ($env:TUT_HOME) { $env:TUT_HOME } else { Join-Path $HOME '.chia-tutorial' }
# gcloud's deepest file is ~165 characters below TUT_HOME, and Windows (and
# Windows PowerShell) stop at 260.
if ($TutHome.Length -gt 80) { Write-Host "ERROR: TUT_HOME is too long for Windows paths; pick a shorter one (now $TutHome)"; exit 1 }
$State = Join-Path $TutHome "state-$Account.sh"
New-Item -ItemType Directory -Force -Path $TutHome | Out-Null
if (-not $Project -and (Test-Path -LiteralPath $State)) {
    # Remembers this account's PROJECT_ID between runs (same format as setup_gcp.sh).
    foreach ($line in Get-Content -LiteralPath $State) {
        if ($line -match '^PROJECT_ID=(.+)$') { $Project = $Matches[1].Trim() }
    }
}

$DevZone = if ($env:DEV_ZONE) { $env:DEV_ZONE } else { 'us-west4-c' }
$DevVm = 'chia-dev'; $DevType = 'c2d-standard-8'; $DevCpu = 'AMD Milan'

function Log([string]$Msg) { Write-Host ''; Write-Host "==> $Msg" -ForegroundColor Cyan }
function Die([string]$Msg) { Write-Host ''; Write-Host "ERROR: $Msg" -ForegroundColor Red; exit 1 }
function Assert-Ok([string]$What) { if ($LASTEXITCODE -ne 0) { Die "$What failed" } }

# --- 1. Private gcloud --------------------------------------------------------
# CLOUDSDK_CONFIG keeps this install's accounts/projects separate from %APPDATA%\gcloud.
$env:CLOUDSDK_CONFIG = Join-Path $TutHome 'gcloud-config'
$Sdk = Join-Path $TutHome 'google-cloud-sdk'
$GcloudPy = Join-Path $Sdk 'lib\gcloud.py'
$Python = Join-Path $Sdk 'platform\bundledpython\python.exe'
# For commands printed for the user to run later in a fresh shell.
$GcloudHint = "`$env:CLOUDSDK_CONFIG='$($env:CLOUDSDK_CONFIG)'; & '$(Join-Path $Sdk 'bin\gcloud.cmd')'"

if ($Purge -and -not (Test-Path -LiteralPath $GcloudPy)) {
    Write-Host "The tutorial's gcloud isn't installed in $TutHome; nothing to sign out of."
    Remove-Item -LiteralPath $State -Force -ErrorAction SilentlyContinue
    exit 0
}
if (-not (Test-Path -LiteralPath $GcloudPy)) {
    Log "Installing a private gcloud CLI into $Sdk"
    # Full paths: in Windows PowerShell "curl" is an alias for Invoke-WebRequest, and
    # a Git for Windows tar.exe on PATH can't unpack zip files.
    $zip = Join-Path $TutHome 'google-cloud-cli.zip'
    & "$env:SystemRoot\System32\curl.exe" -fSL -o $zip `
        'https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-windows-x86_64-bundled-python.zip'
    Assert-Ok 'downloading gcloud'
    & "$env:SystemRoot\System32\tar.exe" -xf $zip -C $TutHome
    Assert-Ok 'unpacking gcloud'
    Remove-Item -LiteralPath $zip
    # No install.bat: gcloud runs as unpacked, and install.bat refuses to run
    # non-interactively with the bundled Python.
    $installGcloud = $true
}

# Run gcloud's Python entry point directly, as gcloud.cmd does, rather than through
# gcloud.cmd: cmd.exe re-parses the arguments and turns Ctrl-C into a "Terminate
# batch job (Y/N)?" prompt.
$env:CLOUDSDK_PYTHON = $Python
$env:PYTHONHOME = $null
$env:Path = "$Sdk\bin\sdk;$env:Path"
function gcloud {
    # Windows PowerShell turns a native command's redirected stderr into error
    # records, which would abort the script under 'Stop'.
    $ErrorActionPreference = 'Continue'
    & $Python -S $GcloudPy @args
}
if ($installGcloud) {
    gcloud config set --installation disable_usage_reporting true *> $null
    Assert-Ok 'installing gcloud'
}

gcloud config set account $Account *> $null

if ($DoTeardown) {
    if ($Project) {
        Log "Deleting $DevVm in $Project"
        gcloud compute instances delete $DevVm --zone $DevZone --project $Project --quiet
    } elseif (-not $Purge) {
        Die "no project recorded in $State"
    }
    if (-not $Purge) {
        Write-Host "To delete the whole project too: $GcloudHint projects delete $Project"
        Write-Host "To also sign out and remove the tutorial's local files: $Self -Account $Account -Purge"
        exit 0
    }

    if ($Project) {
        $answer = Read-Host "Also delete the whole project $Project? [y/N]"
        if ($answer -match '^[yY]') { gcloud projects delete $Project --quiet }
        else { Write-Host "Keeping project $Project." }
    }

    # Take the tutorial's VMs out of ~/.ssh/config if it added that section and this is
    # the last account using TUT_HOME (see below). config-ssh needs valid credentials,
    # so this comes before signing out.
    $marker = Join-Path $TutHome 'ssh-config-preexisting'
    $otherStates = @(Get-ChildItem -LiteralPath $TutHome -Filter 'state-*.sh' | Where-Object { $_.FullName -ne $State })
    if ($otherStates.Count -eq 0 -and (Test-Path -LiteralPath $marker) -and
        (Get-Content -LiteralPath $marker -Raw).Trim() -eq 'no') {
        Log "Removing the tutorial's VMs from ~/.ssh/config"
        gcloud compute config-ssh --remove --quiet *> $null
    }

    Log "Signing $Account out of the tutorial's gcloud"
    gcloud auth revoke $Account --quiet *> $null
    gcloud auth application-default revoke --quiet *> $null

    # Other accounts may share TUT_HOME (and the SSH key); only clean up when this
    # is the last one.
    Remove-Item -LiteralPath $State -Force -ErrorAction SilentlyContinue
    $others = @(Get-ChildItem -LiteralPath $TutHome -Filter 'state-*.sh')
    if ($others.Count -eq 0) {
        # The SSH key is shared with any other gcloud on this machine: only remove
        # it if this tutorial created it.
        $marker = Join-Path $TutHome 'ssh-key-preexisting'
        if ((Test-Path -LiteralPath $marker) -and (Get-Content -LiteralPath $marker -Raw).Trim() -eq 'no') {
            Log 'Removing the SSH key the tutorial created'
            foreach ($f in 'google_compute_engine', 'google_compute_engine.pub', 'google_compute_engine.ppk',
                           'google_compute_known_hosts') {
                Remove-Item -LiteralPath (Join-Path $HOME ".ssh\$f") -Force -ErrorAction SilentlyContinue
            }
        }
        Log "Removing $TutHome"
        Remove-Item -LiteralPath $TutHome -Recurse -Force
    } else {
        Write-Host "Keeping ${TutHome}: it still holds state for: $(($others | ForEach-Object Name) -join ', ')"
    }
    exit 0
}

# --- 2. Sign in --------------------------------------------------------------
gcloud auth print-access-token $Account *> $null
if ($LASTEXITCODE -ne 0) {
    Log "Signing in as $Account (a browser window will open)"
    gcloud auth login $Account --update-adc
    Assert-Ok 'signing in'
}
$active = (gcloud auth list --filter=status:ACTIVE '--format=value(account)' | Out-String).Trim()
if ($active -ne $Account) { Die "signed in as '$active', expected '$Account' (pick that account in the browser)" }
Write-Host "Signed in as $Account"

# --- 3. Project + billing ----------------------------------------------------
if (-not $Project) {
    $chars = 'abcdefghijklmnopqrstuvwxyz0123456789'
    $Project = 'chia-tut-' + -join (1..6 | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    Log "Creating project $Project"
    gcloud projects create $Project '--name=CHIA tutorial'
    if ($LASTEXITCODE -ne 0) {
        Die 'project creation failed (your organization may require a folder or block it; re-run with -Project <existing project>)'
    }
}
Set-Content -LiteralPath $State -Value "PROJECT_ID=$Project" -Encoding Ascii
gcloud config set project $Project *> $null
gcloud auth application-default set-quota-project $Project *> $null

$billing = (gcloud billing projects describe $Project '--format=value(billingEnabled)' | Out-String).Trim()
if ($billing -ne 'True') {
    if (-not $BillingAccount) {
        $accounts = @(gcloud billing accounts list --filter=open=true '--format=value(name.basename())' |
                      Where-Object { $_ })
        if ($accounts.Count -eq 0) {
            Die 'no open billing account. Create one at https://console.cloud.google.com/billing (or redeem the tutorial credits), then re-run.'
        } elseif ($accounts.Count -eq 1) {
            $BillingAccount = $accounts[0]
        } else {
            gcloud billing accounts list --filter=open=true
            Die 'several billing accounts found; re-run with -BillingAccount <ACCOUNT_ID>'
        }
    }
    Log "Linking billing account $BillingAccount"
    gcloud billing projects link $Project --billing-account=$BillingAccount | Out-Null
    Assert-Ok 'linking billing'
}

# --- 4. APIs -----------------------------------------------------------------
# compute: VMs. cloudaicompanion: Gemini Code Assist (what agy signs in against).
# aiplatform: Vertex AI (Gemini via OpenCode, the alternative CHIA backend).
Log 'Enabling APIs (takes a minute the first time)'
while ($true) {
    $out = gcloud services enable compute.googleapis.com cloudaicompanion.googleapis.com `
               aiplatform.googleapis.com --project $Project 2>&1 | ForEach-Object { "$_" } | Out-String
    if ($LASTEXITCODE -eq 0) { break }
    if ($out -notmatch 'UREQ_TOS_NOT_ACCEPTED') { Write-Host $out; Die 'enabling APIs failed' }
    # New accounts must accept the Google Cloud terms once, in the browser.
    Write-Host @"

This account hasn't accepted the Google Cloud Terms of Service yet. Signed in as
$Account, open this page, accept the terms, and come back:

  https://console.developers.google.com/terms/cloud

"@
    Read-Host "Press Enter once you've accepted them" | Out-Null
}

# --- 5. Pre-flight: network, machine types, CPU quota ------------------------
# New projects get a default network (with SSH/internal firewall rules) once the
# Compute API is on, but asynchronously: it can appear while we create our own.
gcloud compute networks describe default *> $null
if ($LASTEXITCODE -ne 0) {
    Log 'Creating the default network + SSH firewall rule'
    $out = gcloud compute networks create default --subnet-mode=auto 2>&1 | ForEach-Object { "$_" } | Out-String
    if ($LASTEXITCODE -eq 0) {
        gcloud compute firewall-rules create default-allow-ssh --network=default `
            --allow=tcp:22 --source-ranges=0.0.0.0/0
        Assert-Ok 'creating the SSH firewall rule'
        gcloud compute firewall-rules create default-allow-internal --network=default `
            '--allow=tcp,udp,icmp' --source-ranges=10.128.0.0/9
        Assert-Ok 'creating the internal firewall rule'
    } else {
        gcloud compute networks describe default *> $null
        if ($LASTEXITCODE -ne 0) { Write-Host $out; Die 'creating the default network failed' }
        Write-Host 'GCP created the default network itself in the meantime'
    }
}

gcloud compute machine-types describe $DevType --zone $DevZone *> $null
if ($LASTEXITCODE -ne 0) {
    $zones = (gcloud compute machine-types list "--filter=name=$DevType" '--format=value(zone)') -join ' '
    Die "$DevType is not offered in $DevZone. Zones that have it: $zones"
}

function Test-Quota([string]$Region, [string]$Metric, [int]$Needed) {
    $json = gcloud compute regions describe $Region --format=json | Out-String
    if ($LASTEXITCODE -ne 0) { return }
    foreach ($q in ($json | ConvertFrom-Json).quotas) {
        if ($q.metric -eq $Metric -and $q.limit - $q.usage -lt $Needed) {
            Write-Host (("WARNING: {0} quota in {1} is {2} (in use {3}); need {4} more. Request an increase at`n" +
                         "  https://console.cloud.google.com/iam-admin/quotas") -f $Metric, $Region, [int]$q.limit, [int]$q.usage, $Needed)
        }
    }
}
$DevRegion = $DevZone -replace '-[^-]*$', ''
Test-Quota $DevRegion C2D_CPUS 8
Test-Quota $DevRegion CPUS 8

# --- 6. VMs ------------------------------------------------------------------
# The VM runs this with bash: write it with LF line endings and no BOM.
$Startup = Join-Path $TutHome 'startup.sh'
$startupText = @'
#!/bin/bash
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
'@
[IO.File]::WriteAllText($Startup, ($startupText -replace "`r", '') + "`n", (New-Object Text.UTF8Encoding $false))

gcloud compute instances describe $DevVm --zone $DevZone *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "$DevVm already exists"
} else {
    # chia-dev gets the cloud-platform scope so CHIA running on it can create more VMs.
    Log "Creating $DevVm ($DevType, $DevCpu) in $DevZone"
    gcloud compute instances create $DevVm --zone $DevZone --machine-type $DevType `
        "--min-cpu-platform=$DevCpu" --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud `
        --boot-disk-type=pd-balanced --boot-disk-size=200GB `
        "--metadata-from-file=startup-script=$Startup" --scopes=cloud-platform
    Assert-Ok "creating $DevVm"
}

# --- 7. Per-user setup on the VM (docker group, conda) ----------------------
# Before the first `gcloud compute ssh` creates it, note whether an SSH key
# already existed so -Purge knows whether it may delete it.
$marker = Join-Path $TutHome 'ssh-key-preexisting'
if (-not (Test-Path -LiteralPath $marker)) {
    $existed = if (Test-Path -LiteralPath (Join-Path $HOME '.ssh\google_compute_engine')) { 'yes' } else { 'no' }
    Set-Content -LiteralPath $marker -Value $existed -Encoding Ascii
}

# Runs a bash script on the VM. On Windows gcloud ssh goes through PuTTY's plink,
# which re-joins and re-quotes arguments, and Windows PowerShell mangles embedded
# double quotes, so the script travels base64-encoded.
function Invoke-Vm([string]$Script) {
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($Script -replace "`r", '')))
    gcloud compute ssh $DevVm --zone $DevZone --quiet --command "echo $b64 | base64 -d | bash"
}

# The docker command appears partway through Docker's install, before its group
# and service exist, so wait for those instead.
Log "Waiting for SSH + Docker on $DevVm (the VM installs Docker on first boot, a few minutes)"
$tries = 0
while ($true) {
    Invoke-Vm 'getent group docker && systemctl is-active --quiet docker' *> $null
    if ($LASTEXITCODE -eq 0) { break }
    $tries++
    if ($tries -ge 60) {
        Die "Docker still isn't running on $DevVm after 10 minutes. See the install log with:
  $GcloudHint compute ssh $DevVm --zone $DevZone --command 'sudo journalctl -u google-startup-scripts --no-pager | tail -50'"
    }
    Start-Sleep -Seconds 10
}
Invoke-Vm 'sudo usermod -aG docker "$USER"'
Assert-Ok 'adding you to the docker group'
Write-Host "${DevVm}: $(Invoke-Vm "lscpu | sed -n 's/^Model name: *//p'")"

Log "Installing conda (Miniforge) on $DevVm"
Invoke-Vm @'
set -e
if [ ! -x ~/miniforge3/bin/conda ]; then
    curl -fsSL https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/miniforge.sh
    bash /tmp/miniforge.sh -b -p ~/miniforge3 >/dev/null
    ~/miniforge3/bin/conda init bash >/dev/null
fi
mkdir -p ~/.gemini
~/miniforge3/bin/conda --version
'@
Assert-Ok 'installing conda'

# --- 8. Gemini / Antigravity -------------------------------------------------
$AgyToken = '.gemini/antigravity-cli/antigravity-oauth-token'
Invoke-Vm "test -s ~/$AgyToken" *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "agy is already signed in on $DevVm (~/$AgyToken exists)"
} else {
    # The login lands in ~/.gemini on $DevVm, which CHIA mounts into its LLM containers.
    Log "Installing agy on $DevVm"
    Invoke-Vm '[ -x ~/.local/bin/agy ] || curl -fsSL https://antigravity.google/cli/install.sh | bash >/dev/null'
    Assert-Ok 'installing agy'
    Log "Signing agy in on $DevVm"
    Write-Host @"
agy will print a sign-in URL. Open it, pick $Account, then choose project
$Project and location 'global'. Once agy shows its prompt you're signed in:
type /exit (or press Ctrl-C) to leave agy and let this script continue.

"@
    # agy is a full-screen terminal app. plink can't drive one from a Windows
    # console, so use Windows' own OpenSSH client with the key gcloud created,
    # and fall back to plink if that isn't available or can't connect.
    $sshExe = "$env:SystemRoot\System32\OpenSSH\ssh.exe"
    $target = (gcloud compute ssh $DevVm --zone $DevZone --dry-run | Out-String) -replace '(?s).*?(\S+@\d+\.\d+\.\d+\.\d+).*', '$1'
    $rc = 255
    if ((Test-Path -LiteralPath $sshExe) -and $target -match '^\S+@[\d.]+$') {
        & $sshExe -t -i (Join-Path $HOME '.ssh\google_compute_engine') -o StrictHostKeyChecking=accept-new `
            -o "UserKnownHostsFile=$(Join-Path $TutHome 'known_hosts')" $target '~/.local/bin/agy'
        $rc = $LASTEXITCODE
    }
    if ($rc -eq 255) {
        # gcloud normally answers plink's host-key prompt by piping "y" to its stdin,
        # which would leave agy no keyboard; the key is already cached by now.
        $env:CLOUDSDK_SSH_PUTTY_FORCE_CONNECT = 'false'
        gcloud compute ssh $DevVm --zone $DevZone --quiet --ssh-flag=-t --command '~/.local/bin/agy'
        $env:CLOUDSDK_SSH_PUTTY_FORCE_CONNECT = $null
    }
    # Leaving agy with Ctrl-C exits non-zero; the token check below is what counts.
    Invoke-Vm "test -s ~/$AgyToken" *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: no agy login found in ~/.gemini on $DevVm; re-run this script to try again."
    }
}

# --- 9. SSH config (VS Code Remote-SSH, plain ssh) ---------------------------
# config-ssh keeps one "Google Compute Engine Section" in ~/.ssh/config and rewrites
# it with this project's running VMs. Note whether one already existed so -Purge
# knows whether it may remove it.
$marker = Join-Path $TutHome 'ssh-config-preexisting'
if (-not (Test-Path -LiteralPath $marker)) {
    $sshConfig = Join-Path $HOME '.ssh\config'
    $existed = if ((Test-Path -LiteralPath $sshConfig) -and
                   (Select-String -LiteralPath $sshConfig -Pattern '^# Google Compute Engine Section' -Quiet)) { 'yes' } else { 'no' }
    Set-Content -LiteralPath $marker -Value $existed -Encoding Ascii
}
Log "Adding $DevVm to ~/.ssh/config"
gcloud compute config-ssh --quiet | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "WARNING: gcloud compute config-ssh failed; VS Code Remote-SSH won't list $DevVm." }
# The config entries have no User line, so ssh logs in as the local username. gcloud
# uses a different one when that has spaces or non-ASCII characters, so spell it out.
# Also spell out the whole connection, for anyone who'd rather not rely on ~/.ssh/config.
$SshHost = "$DevVm.$DevZone.$Project"
$keyArg = "-i `"$(Join-Path $HOME '.ssh\google_compute_engine')`""
$SshDirect = "ssh $keyArg USER@EXTERNAL_IP"
if ((gcloud compute ssh $DevVm --zone $DevZone --dry-run | Out-String) -match '(\S+)@(\d+\.\d+\.\d+\.\d+)') {
    $SshDirect = "ssh $keyArg $($Matches[1])@$($Matches[2])"
    if ($Matches[1] -cne $env:USERNAME) { $SshHost = "$($Matches[1])@$SshHost" }
}

# --- Done ---------------------------------------------------------------------
$EnvFile = Join-Path $TutHome 'env.ps1'
Set-Content -LiteralPath $EnvFile -Encoding UTF8 -Value @(
    "`$env:CLOUDSDK_CONFIG = '$($env:CLOUDSDK_CONFIG -replace "'", "''")'"
    "`$env:Path = '$((Join-Path $Sdk 'bin') -replace "'", "''");' + `$env:Path"
    "`$env:CHIA_TUT_PROJECT = '$Project'"
)
Log 'Done'
Write-Host @"
Project:  $Project
VM:       $DevVm ($DevZone)

Open a shell that uses the tutorial's gcloud:  powershell -ExecutionPolicy Bypass -NoExit -File "$EnvFile"
SSH to your dev machine (from that shell):    gcloud compute ssh $DevVm --zone $DevZone   (opens a PuTTY window)
                                          or: ssh $SshHost
                                          or: $SshDirect
  (stopping and starting the VM changes its IP: re-run gcloud compute config-ssh;
   gcloud compute instances list shows the new IP)
Tear down the development machine:            $Self -Account $Account -Teardown
Remove everything (VM, sign-in, local files): $Self -Account $Account -Purge
"@
