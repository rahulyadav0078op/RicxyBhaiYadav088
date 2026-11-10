> ⚠️ **Ye purana version hai.** Latest, screenshot-level guide: [`RENDER_STEP_BY_STEP.md`](RENDER_STEP_BY_STEP.md)

# Deploy — step by step (Render, zero se live)

Total: ~20 min. Do hisse: **A) code GitHub pe**, **B) Render pe deploy**.
Credentials ka hissa `EASY_SETUP.md` (Raasta 1 = rclone, console zero).

---

# PART A — Code GitHub pe

## A1. ZIP download + extract
`gdrive-telegram-bot.zip` download karo, extract karo. Andar `gdrive-bot/` folder milega
(34 files).

## A2. GitHub repo banao
1. https://github.com/new
2. Repository name: `gdrive-bot` → **Private** ✅ → **Create repository**

## A3. Files upload karo (phone se bhi ho jaayega)
Repo page pe → **Add file → Upload files** → `gdrive-bot/` folder ki **saari files** select
karo → **Commit changes**.

> ⚠️ `.env` **upload mat karna** (wo credentials wali file hai — `.gitignore` me blocked hai).
> `.env.example` upload karna hai.
>
> ⚠️ Folder structure flat hona chahiye — `Dockerfile` repo ke **root** me ho, kisi
> subfolder me nahi. Verify: repo home pe `Dockerfile`, `bot.py`, `requirements.txt`
> dikhne chahiye.

## A4. Check: 3 files zaroori
Repo me ye 3 root me hone chahiye:
- `Dockerfile`
- `requirements.txt`
- `bot.py`

Nahi hain → upload dobara karo root pe.

---

# PART B — Render pe deploy

## B1. Service banao
https://dashboard.render.com → **New + → Blueprint** (agar `render.yaml` upload kiya hai)
ya **New + → Worker** (manual).

**Blueprint raasta (aasan):**
1. **Blueprint** → apna `gdrive-bot` repo select karo
2. Render `render.yaml` padhega → dikhayega: **worker** + **disk 10 GB** + env vars
3. **Apply**

**Manual raasta:**
1. **New + → Worker**
2. Repo connect karo
3. Name: `gdrive-bot` · Runtime: **Docker** · Plan: **Starter** (free plan pe disk nahi milta)
4. **Create**

## B2. Disk add karo — **ye skip mat karna**
Service → **Settings → Disks → Add Disk**
- Name: `bot-data`
- Mount Path: **`/data`**
- Size: 10 GB

Bina iske har redeploy pe "kaun-kaun se file bheje" ledger delete ho jaayega aur 12 GB
course dobara file 1 se shuru hoga.

## B3. Environment variables
Service → **Environment → Environment Variables** → **Add from .env** (ya ek-ek karo):

```env
BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxx
ALLOWED_USER_IDS=123456789

GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
GOOGLE_REFRESH_TOKEN=1//0gxxxxxxxxxxxxxxxx

DATA_DIR=/data
TEMP_DIR=/data/tmp
JOBS_DB=/data/jobs.sqlite3
FOLDER_ORDER=deep
FOLDER_RECURSIVE=true
MAX_FOLDER_FILES=300
MAX_DOWNLOAD_GB=15
MAX_UPLOAD_MB=2000
CONCURRENT_DOWNLOADS=1
KEEP_DOWNLOADED_FILES=false
LOG_LEVEL=INFO
STARTUP_REPORT=true
```

**Kahan se:**
| Value | Kahan |
|---|---|
| `BOT_TOKEN` | Telegram → @BotFather → `/newbot` |
| `ALLOWED_USER_IDS` | Telegram → @userinfobot |
| Google 3 values | `python setup_wizard.py --path rclone` (console zero) **ya** `EASY_SETUP.md` Raasta 2 |

> ⚠️ Values me **quotes nahi** lagane. `"123-abc..."` paste karoge to Google use
> literally padhega aur `invalid_client` aayega.

## B4. Deploy
**Save changes** → Render auto-deploy karega. Manually: **Manual Deploy → Deploy latest commit**.

Build ~3–5 min. **Logs** (Deploys → Logs) me ye dikhna chahiye:

```
• data dir   : /data (writable)
• jobs ledger: /data/jobs.sqlite3
• temp dir   : /data/tmp
Bot up as @your_bot (id 123456789)
```

## B5. Verify — Telegram me (shell ki zaroorat nahi)

Bot ko message karo:

| Command | Expected |
|---|---|
| `/start` | Mode + supported links |
| `/auth` | Account: tumhara Gmail ✅ |
| **`/doctor`** | 10 checks ka result — sab ✅ hona chahiye |
| `/doctor <folder link>` | + folder access check bhi |

`/doctor` me sab ✅ = ready. Koi ❌ = neeche table dekho.

## B6. Course transfer
1. Folder link bhejo: `https://drive.google.com/drive/folders/<FOLDER_ID>`
2. Numbered preview aayega (`1. Module 1/Lesson 1.mp4 — 240 MB` …)
3. **▶️ Send all to Telegram**
4. `/jobs` progress · `/cancel` stop · `/clear` reset

---

# /doctor me error aaye to

| ❌ Check | Fix |
|---|---|
| **webhook SET** | Chat me `/fixwebhook` bhejo — 2 second me fix |
| **Google token / token ↔ client** | Token aur Client ID alag clients ke hain → `setup_wizard.py --path rclone` se naya banao, dono values ek saath update karo, Redeploy |
| **folder access** | Folder bot ke Gmail ko shared nahi — Drive me **Viewer** permission do |
| **Drive scope** | `drive.readonly` scope ke saath dobara authorize |
| **ledger** | Disk `/data` pe mount nahi — B2 dobara |
| **BOT_TOKEN** | BotFather se naya token, quotes hata ke paste |
| Bot bilkul reply nahi | Service "Live" hai? Logs me crash to nahi? Worker (web service nahi) hai? |
| `No such file or directory` | `Dockerfile` root me nahi / files upload nahi hui — A3 dobara |

---

# Update kaise kare (baad me)

**Sabse aasan:** GitHub repo → file edit/upload → Commit → Render auto-deploy (2-3 min).

**Render dashboard se:** Manual Deploy → Deploy latest commit.

Ledger aur downloads disk pe hain, to redeploy se resume nahi tootega.

---

# Cost

- Worker **Starter**: ~$7/month
- Disk 10 GB: ~$2.50/month
- **Free plan pe disk nahi milta** — isliye paid zaroori hai

Sasta chahiye? `GCP_DEPLOY.md` (GCE e2-micro, free tier possible) ya laptop pe
`python bot.py` (bilkul free, bas laptop chalta rahe).

---

# Ek line me

```
ZIP extract → GitHub upload → Render Worker + disk /data → 15 env vars
→ Deploy → Telegram /doctor → sab ✅ → folder link → ▶️ Send all
```
