# RAILWAY — step by step deploy (phone se bhi ho jayega)

Bot ko Railway par 24×7 chalana. Total time ~20 min (Google OAuth alag).

> **Railway basics (Nov 2026):** Hobby plan **$5/mo** me $5 usage included;
> usage ke baad per-second billing (~$20/vCPU-month, ~$10/GB-RAM-month,
> egress $0.05/GB). Volume **$0.15/GB-month**. Region **Singapore
> (`asia-southeast1`)** available hai — India se sabse close.
> Free plan ($1 credit) bot ke liye kaafi nahi — Hobby lo.
>
> ⚠️ Railpack (Railway ka default builder) Docker ke saath conflict karta hai.
> Isliye Step 5 me **builder = DOCKERFILE** set karna zaroori hai — repo me
> `railway.json` hai jo yahi karta hai, par dashboard me confirm kar lena.

---

## STEP 1 — GitHub par code chado (phone se, browser me)

Railway GitHub se deploy karta hai, isliye code pehle GitHub par chahiye.

1. Zip extract karo: **`gdrive-telegram-bot.zip`** → 40+ files milengi
   (`bot.py`, `Dockerfile`, `railway.json`, docs…).
2. Phone browser: **https://github.com/new**
3. Repository name: `gdrive-bot` · **Private** ✅ · **Create repository**
4. Repo page par → **uploading an existing file** link
5. Files drag karo / *choose your files* → **saari files** select karo
   (zip ke andar ka `uploads/` folder chhod do, woh screenshots hain)
   🔴 `Dockerfile`, `railway.json`, `requirements.txt` **zaroor** upload honi chahiye
   (root me, kisi folder ke andar nahi)
6. Commit message: `initial` → **Commit changes**

> GitHub mobile app se bhi ho jayega: App → **+** → *Create new repository* →
> *Add file* → *Upload files*. Lekin browser wala tareeka aasan hai.

---

## STEP 2 — Railway project banao

1. **https://railway.app** → **Login with GitHub**
2. **New Project** → **Deploy from GitHub repo**
3. Agar repo list me na dikhe → **Configure GitHub App** → `gdrive-bot` repo
   ko access do → wapas aao
4. `gdrive-bot` select karo → **Deploy**
5. Pehla build **fail hoga** — normal hai, kyunki abhi token/database nahi hai.
   Log me `BOT_TOKEN is not set` dikhega. Aage badho.

---

## STEP 3 — Volume attach karo (persistent disk)

Bina volume ke har redeploy par "kaun si file bhej di" ledger ud jayega aur
course file 1 se shuru ho jayega.

1. Project canvas me service card **right-click** → **Attach Volume**
   (ya service kholo → **Volumes** tab → **+ New Volume**)
2. Mount path: **`/data`** ← bilkul yahi
3. Size: **10 GB** (course ke hisaab se 20 GB bhi)
4. **Attach / Add**

Railway volume ko `requiredMountPath` se enforce nahi karta jab tak config me na
ho — humne mount path env var se hi use kiya hai, isliye `/data` exactly likho.

---

## STEP 4 — Variables (env) daalo

Service kholo → **Variables** tab → **Raw Editor** (sabse fast) → yeh paste
karke values bharo → **Update Variables**:

```env
BOT_TOKEN=123456789:AA....                # @BotFather se
ALLOWED_USER_IDS=987654321                # @userinfobot se (numeric, @ ke bina)

GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=1//...

DRIVE_SCOPE=readonly
AUTO_QUOTA_FIX=false

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
MEDIA_WRITE_TIMEOUT=1800
STARTUP_REPORT=true
LOG_LEVEL=INFO
```

**Kahan se laayein:**

* `BOT_TOKEN` — Telegram me **@BotFather** → `/newbot` → naam → username →
  token milega.
* `ALLOWED_USER_IDS` — Telegram me **@userinfobot** → *Start* → `Id: 987654321`.
* Google ki 3 lines — [`GOOGLE_OAUTH_PHONE_GUIDE.md`](GOOGLE_OAUTH_PHONE_GUIDE.md).

> Public files only chahiye to Google ki 3 lines khaali chhod do — bot phir bhi
> chalega.

---

## STEP 5 — Builder = Dockerfile (confirm)

1. Service → **Settings** tab
2. **Source** → **Builder**: **Dockerfile** (Railpack nahi)
3. **Dockerfile Path**: `Dockerfile`
4. **Deploy** → **Root Directory**: `/` (khaali)
5. Save → **Redeploy**

`railway.json` yeh khud set karta hai, par dashboard me ek baar dekh lena —
galat builder hone par `ffmpeg not found` jaisi errors aati hain.

---

## STEP 6 — Deploy + logs padho

1. **Deployments** tab → latest deployment → **View Logs**
2. Ye lines dikhni chahiye:

```
• data dir   : /data (writable)
• jobs ledger: /data/jobs.sqlite3
Starting polling…
Bot up as @your_bot (id 123456789)
```

`✗ /data is not writable` aaye to → Volume mount path dobara check karo
(`/data`), phir **Redeploy**.

---

## STEP 7 — Telegram me verify (2 min)

Bot ko kholo → **/start**

| Command | Kya hona chahiye |
|---|---|
| `/start` | welcome + commands list |
| `/auth` | tumhara Gmail (OAuth set hai to) |
| `/doctor` | saari lines ✅ (ffmpeg bhi) |
| `/status` | queue khaali, disk free |

Phir asli test:

1. Ek **public** Drive video ka link bhejo → file Telegram par aani chahiye.
2. Ek **view-only / download-disabled** video: `/streamdl <link>` →
   "Tracks mile → video+audio → joining → ✅".
3. Course folder ka link bhejo (`drive.google.com/drive/folders/...`) →
   preview dikhega → **Start** dabao → poori course sequence me chali jayegi.

---

## STEP 8 — Zaroori settings (production)

| Setting | Value | Kyun |
|---|---|---|
| **Replicas** | **1** | 2 replicas = 2 pollers = duplicate uploads + Telegram 409 |
| **Restart policy** | ALWAYS | crash par wapas uthe |
| **Region** | Singapore | India se lowest latency |
| **Custom start command** | khaali | Dockerfile ka ENTRYPOINT chalta hai (`railway.json` me set hai) |

---

## STEP 9 — Troubleshooting (Railway-specific)

| Symptom | Wajah | Fix |
|---|---|---|
| Deploy "successful" par bot chup | Poller mar gaya / webhook set hai | Logs dekho; Telegram me `/fixwebhook` bhejo |
| `sqlite3.OperationalError: unable to open database file` | Volume root-owned | Entrypoint `chown` karta hai — Volume `/data` par hai na, confirm karo; phir Redeploy |
| `ffmpeg is not installed` | Galat builder (Railpack) ya purana zip | Builder = Dockerfile (Step 5); naya `Dockerfile` use karo |
| Build fail: `No module named bot` | Files folder ke andar chali gayi | Repo root me `bot.py` hona chahiye (`COPY *.py ./` isi par chalta hai) |
| Course har redeploy par file 1 se shuru | `JOBS_DB` volume par nahi | `JOBS_DB=/data/jobs.sqlite3` |
| `409 Conflict` | Do jagah se polling | Replicas 1 karo, purana host band karo |
| 50 MB ke baad upload fail | Public Bot API ka documented ceiling | Bot khud parts me todta hai; 2 GB chahiye to local Bot API server (advanced) |
| Volume full | `MAX_DOWNLOAD_GB` > disk | Cap chhota karo ya volume badhao |
| Har 7 din baad `invalid_grant` | OAuth app Testing mode | Consent screen **Publish app** (Guide Step B.10) |

---

## STEP 10 — Redeploy / update

```
GitHub par file edit/upload → Commit  →  Railway auto-deploy (2-3 min)
```

Ya Railway me: **Deployments** → **⋯** → **Redeploy**.
Volume ka data safe rehta hai — ledger, `.part` files, sab wahi rahenge.

---

## STEP 11 — Self test (optional par recommended)

Railway → service → **⋯** → **Railway Shell** (ya `railway run` CLI):

```bash
python selftest.py
```

Sab ✅ aaye to deployment perfect hai. Ek public video link bhi test kar sakte ho:

```bash
SELFTEST_STREAM_LINK="https://drive.google.com/file/d/XXXX/view" python selftest.py
```

---

## Cost reality (Railway)

| Item | Approx |
|---|---|
| Hobby plan | **$5/mo** (includes $5 usage) |
| 0.5 vCPU + 1 GB RAM 24×7 | ~$10–15/mo usage |
| Volume 10 GB | ~$1.5/mo |
| Egress (course upload) | $0.05/GB — 100 GB ≈ $5 |

Chhota rakhna ho: `CONCURRENT_DOWNLOADS=1`, `MAX_FOLDER_FILES=100`, aur course
complete hone ke baad service **sleep** kar do.

---

**Render par karna hai?** → [`RENDER_STEP_BY_STEP.md`](RENDER_STEP_BY_STEP.md)
