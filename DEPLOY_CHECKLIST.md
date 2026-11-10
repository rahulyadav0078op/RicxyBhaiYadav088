# DEPLOY CHECKLIST — sirf deploy ka kaam (tick karte jao)

Credentials ready hain maan ke chal rahe hain (BOT_TOKEN, ALLOWED_USER_IDS, aur 3 Google values).
Sirf deploy karna hai. Neeche 7 step, ~25 min.

---

## ☐ STEP 1 — GitHub repo (5 min)

- [ ] https://github.com/new kholo
- [ ] Name: `gdrive-bot` · Visibility: **Private** → **Create repository**
- [ ] Repo me **Add file → Upload files**
- [ ] ZIP extract karke `gdrive-bot/` folder ki **saari files** select karo (36 files)
- [ ] **Commit changes**
- [ ] ✅ Verify — repo home pe ye 3 dikhne chahiye: `Dockerfile`, `requirements.txt`, `bot.py`

> ❌ `.env` upload nahi karna (credentials). `.env.example` karna hai.

---

## ☐ STEP 2 — Render service banao (3 min)

- [ ] https://dashboard.render.com → **New +**
- [ ] **Blueprint** chuno (repo me `render.yaml` hai) → `gdrive-bot` repo select → **Apply**

   *Blueprint na dikhe to manual:* **New + → Worker** → repo → Name `gdrive-bot` → Runtime **Docker** → Plan **Starter** → Create

- [ ] Service ban gayi? → **Starter plan** hona chahiye (free plan pe disk nahi milta)

---

## ☐ STEP 3 — Disk add karo (2 min) ⚠️ SABSE ZAROORI

- [ ] Service → **Settings** (ya Disks tab) → **Add Disk**
- [ ] Name: `bot-data`
- [ ] **Mount Path: `/data`**  ← exactly yahi
- [ ] Size: `10 GB` → **Save**

> Iske bina har redeploy pe resume ledger delete → 12 GB course dobara file 1 se shuru.

---

## ☐ STEP 4 — Environment variables (5 min)

- [ ] Service → **Environment** → **Add Environment Variable** (ya "Add from .env")
- [ ] Ye 15 lines paste karo, values apni bharo:

```env
BOT_TOKEN=123456789:AA...
ALLOWED_USER_IDS=123456789
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
GOOGLE_REFRESH_TOKEN=1//0g...
DATA_DIR=/data
TEMP_DIR=/data/tmp
JOBS_DB=/data/jobs.sqlite3
FOLDER_ORDER=deep
FOLDER_RECURSIVE=true
MAX_FOLDER_FILES=300
MAX_DOWNLOAD_GB=15
CONCURRENT_DOWNLOADS=1
STARTUP_REPORT=true
```

- [ ] ⚠️ Values me **quotes nahi** (`"123-abc..."` → `invalid_client` aayega)
- [ ] ⚠️ `DATA_DIR`/`TEMP_DIR`/`JOBS_DB` me **`/data`** hi — kahin aur nahi
- [ ] **Save Changes**

---

## ☐ STEP 5 — Deploy (5 min)

- [ ] Render auto-deploy shuru kar dega; warna **Manual Deploy → Deploy latest commit**
- [ ] **Logs** tab kholo (Live tail)
- [ ] Ye lines dikhni chahiye:

```
• data dir   : /data (writable)
• jobs ledger: /data/jobs.sqlite3
• temp dir   : /data/tmp
Bot up as @your_bot (id 123456789)
```

- [ ] Build fail? → logs ke last 30 lines dekho (usually `Dockerfile` root me nahi hai)
- [ ] Bot crash loop? → credentials ya JSON galat

---

## ☐ STEP 6 — Telegram pe verify (2 min, shell ki zaroorat nahi)

- [ ] Bot ko `/start` bhejo → reply aaya?
- [ ] `/auth` bhejo → **tumhara Gmail** dikhna chahiye
- [ ] **`/doctor`** bhejo → 10 checks, sab ✅ hone chahiye
- [ ] Startup message aaya? (`✅ Bot online — 9/9 checks pass`)

Error aaye to:
| ❌ | Fix |
|---|---|
| webhook SET | `/fixwebhook` bhejo |
| Google token / token ↔ client | credentials ek hi client ke banao → Redeploy |
| folder access | Drive me bot ke Gmail ko Viewer share karo |
| ledger | Step 3 (disk `/data`) dobara |

---

## ☐ STEP 7 — Course transfer (10 sec setup)

- [ ] Folder link bhejo: `https://drive.google.com/drive/folders/<FOLDER_ID>`
- [ ] Numbered preview aayega (files, size, sequence)
- [ ] **▶️ Send all to Telegram** dabao
- [ ] `/jobs` → progress · `/cancel` → stop · `/clear` → reset

---

## Deploy ke baad yaad rakhna

| Cheez | Detail |
|---|---|
| **Cost** | Worker Starter ~$7/mo + disk ~$2.50/mo |
| **Update** | GitHub me file edit → Commit → Render auto-deploy |
| **Logs** | Render → Logs (Live tail) |
| **Redeploy safe** | Disk pe ledger hai — resume nahi tootega |
| **Ek hi instance** | `numInstances=1`; do worker = duplicate uploads |

---

## Ek line

```
GitHub upload → Render Worker → disk /data → 15 env vars → Deploy
→ /doctor sab ✅ → folder link → ▶️ Send all
```
