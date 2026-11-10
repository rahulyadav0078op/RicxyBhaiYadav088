#!/usr/bin/env bash
# deploy_gcp.sh — one-shot deploy of the Drive→Telegram bot to a Google Cloud
# Compute Engine VM (e2-micro, always free tier eligible).
#
#   chmod +x deploy_gcp.sh
#   ./deploy_gcp.sh                 # uses defaults below
#   PROJECT=my-proj ZONE=asia-south1-a ./deploy_gcp.sh
#
# Safe to re-run: existing instance/firewall rules are reused, not recreated.
# This script does NOT put secrets on the command line; it copies .env separately.

set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
ZONE="${ZONE:-asia-south1-a}"          # Mumbai; use us-central1-a for free-tier credit
INSTANCE="${INSTANCE:-gdrive-bot}"
MACHINE="${MACHINE:-e2-micro}"
IMAGE_FAMILY="ubuntu-2404-lts-amd64"
IMAGE_PROJECT="ubuntu-os-cloud"
DISK_GB="${DISK_GB:-30}"               # e2-micro free tier allows 30 GB standard PD

die() { echo "✗ $*" >&2; exit 1; }
say() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }

command -v gcloud >/dev/null || die "gcloud CLI not found: https://cloud.google.com/sdk/docs/install"
[ -n "$PROJECT" ] || die "No project set. Run: gcloud config set project YOUR_PROJECT_ID"

if [ ! -f .env ]; then
  die ".env missing. cp .env.example .env and fill BOT_TOKEN (+ Google creds) first."
fi

say "Project: $PROJECT   Zone: $ZONE   Instance: $INSTANCE   Disk: ${DISK_GB}GB"

# 1. APIs ---------------------------------------------------------------
say "Enabling Compute Engine API (idempotent)"
gcloud services enable compute.googleapis.com --project "$PROJECT" >/dev/null

# 2. Firewall (no inbound ports needed for polling; SSH already open) ---
say "Checking firewall for SSH"
if ! gcloud compute firewall-rules describe allow-ssh --project "$PROJECT" >/dev/null 2>&1; then
  gcloud compute firewall-rules create allow-ssh \
    --project "$PROJECT" --allow tcp:22 --source-ranges 0.0.0.0/0 >/dev/null
  echo "  created allow-ssh"
else
  echo "  allow-ssh already present"
fi

# 3. Instance ----------------------------------------------------------
if gcloud compute instances describe "$INSTANCE" --zone "$ZONE" --project "$PROJECT" >/dev/null 2>&1; then
  say "Instance $INSTANCE already exists — reusing"
else
  say "Creating $INSTANCE ($MACHINE)"
  gcloud compute instances create "$INSTANCE" \
    --project "$PROJECT" --zone "$ZONE" \
    --machine-type "$MACHINE" \
    --image-family "$IMAGE_FAMILY" --image-project "$IMAGE_PROJECT" \
    --boot-disk-size "${DISK_GB}GB" --boot-disk-type pd-standard \
    --metadata=enable-oslogin=TRUE \
    --tags=bot >/dev/null
fi

# 4. Wait for SSH ------------------------------------------------------
say "Waiting for SSH to come up"
for i in $(seq 1 30); do
  if gcloud compute ssh "$INSTANCE" --zone "$ZONE" --project "$PROJECT" \
       --command "echo ready" --quiet >/dev/null 2>&1; then
    echo "  ssh ready"; break
  fi
  [ "$i" -eq 30 ] && die "SSH never became reachable"
  sleep 5
done

# 5. Copy code (tar over ssh: works without gcloud scp quirks) ---------
say "Copying project files"
TMP_TAR="$(mktemp -t bot.XXXXXX.tar.gz)"
tar --exclude='.git' --exclude='__pycache__' --exclude='*.sqlite3' \
    --exclude='.pytest_cache' --exclude='data' \
    -czf "$TMP_TAR" \
    bot.py gdrive_core.py oauth_setup.py railway_setup.py healthcheck.py \
    requirements.txt .env Dockerfile 2>/dev/null \
  || die "tar failed — run this from the project directory"
gcloud compute ssh "$INSTANCE" --zone "$ZONE" --project "$PROJECT" \
  --command "mkdir -p ~/gdrive-bot" --quiet
gcloud compute scp "$TMP_TAR" "$INSTANCE:~/gdrive-bot/app.tar.gz" \
  --zone "$ZONE" --project "$PROJECT" --quiet
rm -f "$TMP_TAR"

# 6. Install on the VM -------------------------------------------------
say "Installing Python venv + systemd service (this takes ~2 min)"
gcloud compute ssh "$INSTANCE" --zone "$ZONE" --project "$PROJECT" --quiet --command '
set -e
cd ~/gdrive-bot && tar -xzf app.tar.gz && rm -f app.tar.gz
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip >/dev/null
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt
mkdir -p data/tmp data/logs
# swap: e2-micro has 1 GB RAM; pip/build steps are happier with a little swap
if [ ! -f /swapfile ]; then
  sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi
sudo tee /etc/systemd/system/gdrive-bot.service >/dev/null <<EOF
[Unit]
Description=Google Drive to Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=/home/$USER/gdrive-bot
EnvironmentFile=/home/$USER/gdrive-bot/.env
Environment=DATA_DIR=/home/$USER/gdrive-bot/data
Environment=TEMP_DIR=/home/$USER/gdrive-bot/data/tmp
Environment=JOBS_DB=/home/$USER/gdrive-bot/data/jobs.sqlite3
ExecStartPre=/home/$USER/gdrive-bot/.venv/bin/python railway_setup.py
ExecStart=/home/$USER/gdrive-bot/.venv/bin/python bot.py
Restart=always
RestartSec=10
StandardOutput=append:/home/$USER/gdrive-bot/data/logs/bot.log
StandardError=append:/home/$USER/gdrive-bot/data/logs/bot.log

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable gdrive-bot >/dev/null 2>&1 || true
sudo systemctl restart gdrive-bot
sleep 6
systemctl is-active gdrive-bot
tail -n 12 data/logs/bot.log || true
'

say "Done. Useful commands:"
cat <<EOF
  gcloud compute ssh $INSTANCE --zone $ZONE --command "sudo journalctl -u gdrive-bot -f"
  gcloud compute ssh $INSTANCE --zone $ZONE --command "tail -f ~/gdrive-bot/data/logs/bot.log"
  gcloud compute ssh $INSTANCE --zone $ZONE --command "sudo systemctl restart gdrive-bot"
  # update code later:
  bash deploy_gcp.sh
EOF
