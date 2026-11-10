# Google Cloud deploy — step by step

Haan, Google Cloud pe **achhe se** chalega. Aur ek bonus: tumhara Google Cloud
project jo Drive API ke liye already bana hai, wahi project bot ke liye use ho
jaata hai — do jagah kaam nahi.

Teen options hain. Confusion se bachne ke liye pehle table padho:

| Option | Kya hai | Bot ke liye? | Cost |
|---|---|---|---|
| **A. Compute Engine VM** (`e2-micro`) | Poora Linux VM, tumhara control | ✅ **Best** — 24×7, unlimited disk, resume ledger safe | Free tier eligible (30 GB disk + e2-micro), warna ~₹600/month |
| **B. Cloud Run (service)** | Container, request-driven | ⚠️ **Trap** — 15 min idle timeout, request aane par hi start, restart pe disk gaya | Free quota decent, par bot ke liye galat |
| **C. Cloud Run (worker pool)** | Container, no HTTP, always-on | ✅ Chalega, par Cloud SQL/size limits | ~$15-25/month (min 1 instance) |

**Mera suggestion: Option A.** Ek command me poora setup ho jaata hai, aur course
download jaise long-running kaam ke liye yahi sahi hai.

---

## Option A — Compute Engine VM (recommended)

### A0. Zaroori cheezein (2 minute)

```bash
# gcloud CLI install: https://cloud.google.com/sdk/docs/install
gcloud auth login
gcloud config set project YOUR_PROJECT_ID     # wahi project jisme Drive API enable kiya tha
gcloud config set compute/region asia-south1
gcloud config set compute/zone asia-south1-a  # Mumbai
```

Billing enabled hona chahiye (free tier ke liye bhi card verify lagta hai).

### A1. `.env` ready karo (local pe)

Phase A wala Google setup pehle kar lo (README §3 ya `RAILWAY_DEPLOY.md` Phase A)
— `GOOGLE_REFRESH_TOKEN` ya service account JSON. Phir:

```bash
cp .env.example .env
# BOT_TOKEN, ALLOWED_USER_IDS, GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN bharo
python bot.py --check        # locally verify: creds OK
```

### A2. Deploy — ek command

```bash
chmod +x deploy_gcp.sh
./deploy_gcp.sh
```

Script ye karta hai (sab idempotent — dobara chalane pe duplicate nahi banta):

1. Compute Engine API enable
2. SSH firewall rule check/create
3. `e2-micro` VM banana (Ubuntu 24.04, **30 GB disk**) — free-tier eligible
4. SSH ready hone tak wait
5. Code VM pe copy (`.git`, `data/`, sqlite exclude)
6. VM pe: `python3-venv`, venv, `pip install -r requirements.txt`, **1 GB swapfile**
   (e2-micro me sirf 1 GB RAM hai, swap zaroori hai)
7. `systemd` service `gdrive-bot` — `Restart=always`, logs `data/logs/bot.log`
8. Service start karke status + last 12 log lines dikhana

Custom values chahiye to:

```bash
PROJECT=my-proj ZONE=us-central1-a MACHINE=e2-small DISK_GB=50 ./deploy_gcp.sh
```

> **Free tier tip:** `e2-micro` + 30 GB standard disk sirf `us-central1`,
> `us-west1`, `us-east1` me free hai. Mumbai (`asia-south1`) me ~₹600/month
> lagega. India se latency behtar chahiye to Mumbai rakho; paisa bachana hai to
> `us-central1-a` — Telegram upload me farak barely noticeable hai.

### A3. Verify

```bash
# live logs
gcloud compute ssh gdrive-bot --zone asia-south1-a \
  --command "tail -f ~/gdrive-bot/data/logs/bot.log"

# service status
gcloud compute ssh gdrive-bot --zone asia-south1-a \
  --command "systemctl status gdrive-bot --no-pager"
```

Logs me ye dikhna chahiye:

```
Google credentials loaded (authorized mode).
Bot up as @your_bot (id 123456789)
```

Telegram pe `/auth` → tumhara Gmail dikhe = ready.

### A4. Course transfer

1. Course folder link bot ko bhejo
2. Numbered preview aayega — `1. Module 1/Lesson 1.mp4 …`
3. **▶️ Send all to Telegram**
4. `/jobs` progress, `/cancel` stop, `/clear` reset ledger

**Disk math:** `MAX_DOWNLOAD_GB` (default 20) 30 GB disk pe theek hai, kyunki
ek waqt me ek hi file disk pe hoti hai (upload ke baad delete). 12 GB course
safely chalega.

### A5. Update / restart / logs

```bash
# naya code deploy (same script — files overwrite ho jaate hain)
./deploy_gcp.sh

# restart
gcloud compute ssh gdrive-bot --zone asia-south1-a \
  --command "sudo systemctl restart gdrive-bot"

# VM band karo (paisa bachega, disk ka thoda charge rahega)
gcloud compute instances stop gdrive-bot --zone asia-south1-a

# poora delete
gcloud compute instances delete gdrive-bot --zone asia-south1-a
```

### A6. Security (VM)

- Bot polling mode me chalta hai → **koi inbound port khula nahi** chahiye (sirf SSH).
- SSH OS Login se hai (`--metadata=enable-oslogin=TRUE`), key management Google ke haath.
- `.env` VM pe `~/gdrive-bot/.env` me hai, `600` permission recommended:
  `gcloud compute ssh ... --command "chmod 600 ~/gdrive-bot/.env"`
- Service `$USER` se chalti hai (root se nahi).
- Refresh token revoke kabhi bhi: https://myaccount.google.com/permissions

---

## Option B — Cloud Run (service) — kyun bot ke liye galat hai

Cloud Run "request-driven" hai. Bot ko koi request nahi aati (wo khud Telegram
se polling karta hai), isliye:

| Cloud Run ka behaviour | Bot pe asar |
|---|---|
| Idle hone par instance **0 tak scale down** | Bot offline ho jaata hai, messages ka reply nahi |
| Request aane par cold start | Pehla message 10-30 s late |
| **15 min** ke baad request timeout | 2 GB video upload beech me mar jaata hai |
| **In-memory filesystem**, restart pe khaali | `/data` ledger + `.part` files gayab → course dobara shuru se |
| `min-instances=1` rakho to | 24×7 bill (~$15+), par phir bhi request-timeout issue |

Agar phir bhi Cloud Run chahiye (Option C, worker pool):

```bash
gcloud run worker-pools deploy gdrive-bot \
  --region asia-south1 \
  --source . \
  --set-env-vars BOT_TOKEN=...,ALLOWED_USER_IDS=...,FOLDER_ORDER=deep,HEALTHCHECK_ENABLED=false \
  --set-secrets GOOGLE_REFRESH_TOKEN=gdrive-refresh-token:latest \
  --min-instances 1
```

Par Cloud Run me **persistent disk nahi** milta — resume ledger ke liye Cloud SQL
ya GCS jaroori hai (is bot me abhi local sqlite hai). Isi liye VM easy aur sasta
padta hai.

---

## Google Cloud ke extra fayde (jo Railway me nahi the)

1. **Same project** — Drive API, OAuth client, bot: sab ek jagah, ek bill.
2. **No egress bill for download** ki tension: GCP egress free tier 200 GB/month
   tak, aur Drive download GCP ke andar hi rehta hai (fast).
3. **Static IP optional** — polling bot ko public IP ki zaroorat nahi, par chaho
   to `gcloud compute addresses create` se de sakte ho.
4. **Secret Manager** — `.env` file ke bajaye secrets manage kar sakte ho (chaho
   to batao, main `--set-secrets` wala refactor bhi kar dunga).
5. **Monitoring** — VM metrics free, `systemd` restart + uptime check se 24×7.

---

## Troubleshooting (GCP-specific)

| Symptom | Fix |
|---|---|
| `deploy_gcp.sh`: `No project set` | `gcloud config set project YOUR_ID` |
| `SSH never became reachable` | VM boot ho raha hai; 1 min baad script dobara chalao (idempotent hai) |
| Logs me `No Google credentials` | `.env` VM pe copy hui par khaali hai — `deploy_gcp.sh` dubara chalao `.env` bhar ke |
| Bot restart loop | `tail -50 ~/gdrive-bot/data/logs/bot.log` — usually bad token ya invalid JSON |
| `unable to open database file` | start command me `railway_setup.py` hai; check `DATA_DIR` writable |
| Upload ke waqt `Killed` | RAM khatam — swap check karo: `free -h`; `CONCURRENT_DOWNLOADS=1` rakho |
| Disk full | `MAX_DOWNLOAD_GB` kam karo, ya `df -h` dekh ke `data/tmp` saaf karo |
| Free tier ke baad bill | VM stop karo jab use na ho; ya `us-central1-a` + e2-micro rakho |

---

## Ek line me jawaab

**Railway pe ho sakta hai, Google Cloud pe bhi ho sakta hai — aur Google Cloud
VM (Option A) is kaam ke liye behtar hai** kyunki long downloads, persistent disk
aur 24×7 chalna wahan natural hai. `./deploy_gcp.sh` chalao, 5 minute me live.
