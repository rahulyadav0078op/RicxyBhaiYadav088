# Render pe deploy — DEEP step-by-step (screenshots-level)

Ye guide zero se live bot tak hai. Har step me: **kahan click, kya paste, kya dikhna chahiye,
aur galat ho to kya**.

**Pehle samajh lo (2 min):**
- Bot = **Worker** (background process). **Web service nahi** — bot ko koi HTTP request
  nahi aati, wo khud Telegram se poochta hai. Web service banayi to idle pe so jaayegi
  aur bada upload 30s timeout pe marr jaayega.
- **Disk `/data`** zaroori hai — resume ledger + adhuri downloads wahan rehti hain.
- Free plan pe **disk nahi milta** → **Starter** plan (~$7/mo).

---

# PART 0 — Pre-flight (5 min)

Ye 4 cheezein ready rakho, warna beech me atak jaoge:

| # | Cheez | Kahan se | Format |
|---|---|---|---|
| 1 | `BOT_TOKEN` | Telegram → @BotFather → `/newbot` | `123456789:AAH...` |
| 2 | `ALLOWED_USER_IDS` | Telegram → @userinfobot | `123456789` |
| 3 | 3 Google values | `python setup_wizard.py --path rclone` se | `...apps.googleusercontent.com`, `GOCSPX-...`, `1//0g...` |
| 4 | GitHub account | github.com | — |

**Google values ka test (deploy se pehle):**
```bash
python setup_wizard.py --check-only
```
`✓ token valid, scope: ...drive.readonly` aaya = aage badho. ❌ aaya = pehle wo fix karo
(`EASY_SETUP.md`), warna Render pe bhi wahi error aayega.

---

# PART 1 — Code GitHub pe (5 min)

## 1.1 ZIP download + extract
`gdrive-telegram-bot.zip` download karo → extract → andar `gdrive-bot/` folder (38 files).

## 1.2 Repo banao
1. https://github.com/new
2. **Repository name:** `gdrive-bot`
3. **Visibility:** Private ✅
4. **Create repository**

## 1.3 Files upload karo
1. Repo page pe → **Add file** → **Upload files**
2. `gdrive-bot/` folder kholo → sabhi files select karo (Ctrl+A) → drag-drop karo
3. **Commit changes** dabao

## 1.4 ✅ Verify — ye 4 files repo ke **root** me dikhne chahiye

```
Dockerfile
requirements.txt
bot.py
render.yaml
```

Subfolder me hain? (`gdrive-bot/gdrive-bot/Dockerfile`) → Render build fail karega.
Fix: files dobara upload karo bina folder ke, ya repo me move karo.

> ❌ `.env` upload **nahi** karna (usme credentials hote hain). `.env.example` karna hai.

---

# PART 2 — Render service banao (5 min)

## 2.1 Login
https://dashboard.render.com → **Sign in with GitHub** → repo access allow karo.

## 2.2 Blueprint (recommended — sab kuch automatic)

1. Top-right **New +** → **Blueprint**
2. **Connect repository** → `gdrive-bot` chuno
3. Render `render.yaml` padhega aur dikhayega:
   - Type: **Worker**
   - Plan: **Starter**
   - Disk: `bot-data` → `/data` (10 GB)
   - Env vars: 20 (kuch `sync: false` = tumse poochhega)
4. **Apply** dabao

Blueprint na mile / error de to **2.3 manual** karo.

## 2.3 Manual (agar Blueprint na chale)

1. **New +** → **Worker**
2. Repository: `gdrive-bot` → **Connect**
3. **Name:** `gdrive-bot`
4. **Region:** Singapore (India ke sabse kareeb)
5. **Branch:** `main`
6. **Runtime:** **Docker**  ← (Python nahi!)
7. **Instance Type:** **Starter** ($7/mo) — free plan pe disk nahi milta
8. **Create Web Service** / **Deploy**

---

# PART 3 — Disk add karo ⚠️ SABSE ZAROORI (2 min)

**Kyun:** bina disk ke har deploy pe ledger delete → 12 GB course dobara file 1 se shuru.

1. Service kholo → **Settings** (left menu)
2. Neeche scroll → **Disks** section → **Add Disk**
3. Bhaро:
   - **Name:** `bot-data`
   - **Mount Path:** `/data`   ← exactly yahi, capital D, slash ke saath
   - **Size:** `10 GB` (course bada hai to 20)
4. **Save**

⚠️ Mount path `/data` ke alawa kuch bhi (jaise `/tmp` ya `/app/data`) → bot ko ledger
writable nahi milega aur `/doctor` me ❌ `ledger` aayega.

---

# PART 4 — Environment variables (5 min)

Service → **Environment** (left menu) → **Add Environment Variable** (ya **Edit** / **Add from .env**).

## 4.1 Ye poora block paste karo (values apni bharo)

```env
# ---- Telegram ----
BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxx
ALLOWED_USER_IDS=123456789

# ---- Google credentials (teeno EK HI client ke) ----
GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
GOOGLE_REFRESH_TOKEN=1//0gxxxxxxxxxxxxxxxx

# ---- paths (disk mount ke andar) ----
DATA_DIR=/data
TEMP_DIR=/data/tmp
JOBS_DB=/data/jobs.sqlite3

# ---- behaviour ----
FOLDER_ORDER=deep
FOLDER_RECURSIVE=true
MAX_FOLDER_FILES=300
MAX_DOWNLOAD_GB=15
MAX_UPLOAD_MB=2000
CONCURRENT_DOWNLOADS=1
KEEP_DOWNLOADED_FILES=false
STARTUP_REPORT=true
MEDIA_WRITE_TIMEOUT=1800
LOG_LEVEL=INFO
```

## 4.2 Value daalne ke 4 rules (yahin 90% galtiyan hoti hain)

| Rule | Galat | Sahi |
|---|---|---|
| **Quotes nahi** | `"123-abc...apps..."` | `123-abc...apps...` |
| **Spaces nahi** | `GOCSPX-abc ` | `GOCSPX-abc` |
| **Newline nahi** | do line me paste | ek line |
| **`\n` escapes sirf JSON var me** | `GOOGLE_SERVICE_ACCOUNT_JSON` me `\n` theek hai | baaki me nahi |

## 4.3 Service account use kar rahe ho? (OAuth ki jagah)

OAuth ki 3 lines hata do, aur ek line daalo:
```env
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"...@....iam.gserviceaccount.com"}
```
Poora JSON **ek line** me. Bot build ke waqt validate karta hai.

## 4.4 Save
**Save Changes** → Render auto-redeploy karega.

---

# PART 5 — Deploy + logs padhna (5 min)

## 5.1 Deploy shuru
Save karte hi deploy chalu. Manually: **Manual Deploy** → **Deploy latest commit**.

## 5.2 Logs dekho
Left menu → **Logs** → toggle **Live tail**.

**Build phase (~3–5 min):**
```
==> Building Docker image
Step 1/12 : FROM python:3.12-slim
...
Successfully built abc123
```

**Runtime phase — ye lines CHAHIYE:**
```
• data dir   : /data (writable)
• jobs ledger: /data/jobs.sqlite3
• temp dir   : /data/tmp
Google credentials loaded (authorized mode).
Bot up as @your_bot (id 123456789)
Starting polling…
```

Aur Telegram pe tumhe ek message aayega:
```
✅ Bot online — 11/11 checks pass
```
(ye `STARTUP_REPORT=true` karta hai — bilkul yahi point hai: pata chale bot zinda hai)

## 5.3 Logs me error patterns → matlab

| Log me dikhe | Matlab | Fix |
|---|---|---|
| `data dir : ... NOT WRITABLE` | disk mount nahi hua | PART 3 dobara |
| `No Google credentials` | env vars save nahi hue / typo | PART 4 |
| `invalid_client` | token aur client id alag clients ke | `setup_wizard.py --path rclone` |
| `unable to open database file` | volume ownership | entrypoint fix karta hai; phir bhi aaye → Custom Start Command `/app/docker-entrypoint.sh` |
| `ModuleNotFoundError` | file upload nahi hui | PART 1.3 dobara |
| `Dockerfile not found` | files root me nahi | PART 1.4 |
| Build me `pip install` fail | repo me `requirements.txt` nahi | upload karo |

---

# PART 6 — Verify (2 min, Telegram me — Render me jaane ki zaroorat nahi)

Bot ko message karo:

| Command | Kya hona chahiye |
|---|---|
| `/start` | welcome + `Mode: authorized` |
| `/auth` | `Account: tumhara@gmail.com` ✅ |
| **`/doctor`** | 11 checks, sab ✅ |
| `/doctor <folder link>` | + `folder access ✅ 87 entries visible` |

`/doctor` output aisa dikhega:
```
✅ BOT_TOKEN — 123456… (len 46)
✅ Telegram token — @your_bot (id 123)
✅ webhook — not set (polling ok, 0 pending)
✅ Google token — refresh works
✅ token ↔ client — match
✅ Drive scope — drive.readonly
✅ account — you@gmail.com (OAuth user)
✅ temp dir — /data/tmp
✅ ledger — /data/jobs.sqlite3
✅ disk free — 8.4 GB
```

**Koi ❌ aaye to** usi line ke aage hint milega, aur `AUDIT_REPORT.md` / `DEPLOY_CHECKLIST.md`
ki table me fix.

---

# PART 7 — Course transfer (10 sec setup + waiting)

1. Folder link bhejo:
   `https://drive.google.com/drive/folders/1OoGb2u2v9o9SbO1eACZ10Pa4GzV--SO4`
2. Bot scan karega (subfolders included) → numbered preview:
   ```
   Files: 87   Total: 12.4 GB   Already sent: 0
   1. Module 1/Lesson 1.mp4 — 240.5 MB
   2. Module 1/Lesson 2.mp4 — 198.0 MB
   ...
   ```
3. **▶️ Send all to Telegram** dabao
4. `[1/87]`, `[2/87]` … aise ek-ek karke sequence me aayega
5. `/jobs` → progress · `/cancel` → current file ke baad stop · `/clear` → ledger reset

**Beech me restart ho gaya?** Wahi folder link dobara bhejo → jahan ruka tha wahin se.

---

# PART 8 — Update / maintenance

| Kaam | Kaise |
|---|---|
| **Code update** | GitHub → file edit/upload → Commit → Render auto-deploy (2–3 min) |
| **Manually deploy** | Render → Manual Deploy → Deploy latest commit |
| **Logs dekhna** | Render → Logs → Live tail |
| **Restart** | Manual Deploy, ya Settings → Suspend/Resume |
| **Band karna** | Settings → **Suspend** (bill rukta hai, disk safe) |
| **Poora delete** | Settings → Delete Service (disk bhi jaayega) |

Redeploy safe hai — ledger disk pe hai, resume nahi tootega.

---

# PART 9 — Cost

| Item | Price |
|---|---|
| Worker Starter | ~$7 / month |
| Disk 10 GB | ~$2.50 / month |
| **Total** | **~$9.50 / month** (~₹800) |

Sasta chahiye:
- **Laptop** pe `python bot.py` — $0 (bas transfer ke dauraan laptop chalta rahe)
- **GCP e2-micro** — free tier possible (`GCP_DEPLOY.md`)

---

# PART 10 — Troubleshooting (jo maine khud verify kiya)

| Problem | Wajah | Fix |
|---|---|---|
| Bot reply nahi karta | webhook registered, ya worker dead | `/fixwebhook`, phir `/doctor` |
| 200 MB+ video upload fail | public API ka documented 50 MB bot limit | bot auto-split karega, ya local Bot API server (`BOT_API_BASE_URL` + `MAX_UPLOAD_MB=2000`) |
| Upload 20 s me cut | `media_write_timeout` (PTB default 20 s) | `MEDIA_WRITE_TIMEOUT=1800` (default hai, par check karo) |
| Folder scan me 403 | folder bot ke account ko shared nahi | Drive → share → Viewer |
| 7 din baad `/auth` fail | OAuth app Testing mode | Cloud Console → **Publish app** (ya rclone se token) |
| Duplicate files | 2 instances chal rahe | `numInstances=1` |
| Disk full | temp cleanup off | `KEEP_DOWNLOADED_FILES=false`, `MAX_DOWNLOAD_GB` kam |
| Course file 1 se shuru | disk mount nahi | PART 3 |

---

# Ek line summary

```
Pre-flight (creds test) → GitHub upload → Render Worker (Starter) → disk /data
→ 20 env vars (quotes nahi) → deploy → logs "Bot up as @…"
→ Telegram /doctor sab ✅ → folder link → ▶️ Send all
```
