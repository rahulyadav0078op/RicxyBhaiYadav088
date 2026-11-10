# RENDER — step by step deploy (phone se bhi ho jayega)

Bot ko Render par 24×7 chalana. Total time ~20 min (Google OAuth alag).

> **Render basics (Nov 2026):** Hobby workspace plan $0, par **Background
> Worker** paid instance hi milta hai — **Starter ~$7/mo** (0.5 vCPU, 512 MB).
> Persistent disk **$0.25/GB-month**. Region **Singapore** available hai.
> Free instances sirf web services ke liye hote hain aur 15 min idle par so
> jaate hain — bot ke liye bekaar.

**Kyun `worker` aur `web service` nahi:** bot khud Telegram ko poll karta hai,
koi HTTP request receive nahi karta. Web service free plan par so jaata hai aur
router timeout bade uploads ko kaat deta hai. Worker bas chalta rehta hai.

**Do raaste hain — A (blueprint, sabse aasan) ya B (manual worker). Neeche
dono hain. A try karo; fail ho to B.**

---

## STEP 1 — GitHub par code chado (dono raaste ke liye zaroori)

1. Zip extract karo: **`gdrive-telegram-bot.zip`**
2. Phone browser: **https://github.com/new** → name `gdrive-bot` →
   **Private** ✅ → **Create repository**
3. **uploading an existing file** → saari files select karo
   (`uploads/` folder chhod do)
   🔴 Root me hona chahiye: `Dockerfile`, `render.yaml`, `requirements.txt`, `bot.py`
4. **Commit changes**

---

# RASTA A — Blueprint (`render.yaml` already hai)

## A1. New Blueprint

1. **https://dashboard.render.com** → login with GitHub
2. **New +** → **Blueprint**
3. Repo connect karo → `gdrive-bot` select karo → **Connect**
4. Render `render.yaml` padh kar service dikhayega:
   **`gdrive-telegram-bot`**, type **Background Worker**, region **Singapore**,
   disk **10 GB** at `/data`, plan **Starter**

## A2. Secrets bharo

Blueprint poochhega jo `sync: false` hain:

| Key | Value kahan se |
|---|---|
| `BOT_TOKEN` | @BotFather (Telegram) |
| `ALLOWED_USER_IDS` | @userinfobot → numeric id |
| `GOOGLE_CLIENT_ID` | [`GOOGLE_OAUTH_PHONE_GUIDE.md`](GOOGLE_OAUTH_PHONE_GUIDE.md) |
| `GOOGLE_CLIENT_SECRET` | wahi guide |
| `GOOGLE_REFRESH_TOKEN` | wahi guide (`1//…`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | khaali chhod do (OAuth kaafi hai) |

→ **Apply** dabao. Render build + deploy karega (~3-5 min).

## A3. Aage badho → **STEP 3** (logs check)

---

# RASTA B — Manual Background Worker (blueprint na chale to)

## B1. Service banao

1. **New +** → **Background Worker**
2. **Build and deploy from a Git repository** → Connect → `gdrive-bot`
3. Settings:

| Field | Value |
|---|---|
| Name | `gdrive-telegram-bot` |
| Region | **Singapore** |
| Branch | `main` |
| Runtime | **Docker** |
| Dockerfile Path | `./Dockerfile` |
| Docker Build Context Directory | `.` |
| Plan | **Starter** ($7/mo) |
| Instance Count | **1** |

> `ffmpeg` ki zaroorat hai isliye **Docker** runtime chuno, Python runtime nahi.
> Docker image `apt-get install ffmpeg` khud karta hai.

4. **Advanced** kholo → **Add Disk**:

| Field | Value |
|---|---|
| Name | `bot-data` |
| Mount Path | **`/data`** |
| Size | **10 GB** |

5. **Add Environment Variables** — yeh paste karo (values bharo):

```
BOT_TOKEN=
ALLOWED_USER_IDS=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
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

6. **Create / Deploy Background Worker**

---

## STEP 3 — Logs check

Service → **Logs** tab. Ye lines chahiye:

```
• data dir   : /data (writable)
• jobs ledger: /data/jobs.sqlite3
Starting polling…
Bot up as @your_bot (id 123456789)
```

* `✗ /data is not writable` → disk mount path `/data` confirm karo → **Manual
  Deploy → Clear build cache & deploy**
* `BOT_TOKEN is not set` → Variables tab me value khaali hai

---

## STEP 4 — Telegram me verify

1. Bot kholo → **/start**
2. `/doctor` → saari lines ✅ (ffmpeg bhi dikhega)
3. `/auth` → tumhara Gmail (OAuth set kiya ho to)
4. Ek **public video link** bhejo → file aa jayegi
5. `/streamdl <view-only video link>` → video+audio tracks download → join → send
6. Course folder link (`/drive/folders/...`) bhejo → **Start** → poora course
   sequence me upload

---

## STEP 5 — Zaroori settings

| Setting | Value | Kyun |
|---|---|---|
| **Instance Count** | **1** | 2 pollers = duplicate upload + 409 |
| **Auto-Deploy** | Yes | git push par update |
| **Region** | Singapore | India ke closest |
| **Health Check Path** | khaali | worker ka koi port nahi (bot khud report bhejta hai) |

---

## STEP 6 — Render-specific troubleshooting

| Symptom | Wajah | Fix |
|---|---|---|
| Build fail: `python: not found` | Runtime Python chuna, Docker nahi | Settings → **Docker** |
| `ffmpeg is not installed` | Purana image / Python runtime | Docker runtime + current `Dockerfile` |
| `unable to open database file` | Disk root-owned | Entrypoint chown karta hai; mount path `/data` confirm karo |
| Bot deploy hua par silent | Free instance so gaya / webhook set hai | Paid instance; Telegram me `/fixwebhook` |
| `Exited with status 1` on boot | Token ya module missing | Logs me last lines; `python env_check.py` shell me chalao |
| Har redeploy par course restart | Disk attach nahi | Add Disk → `/data`; `JOBS_DB=/data/jobs.sqlite3` |
| Upload 50 MB par ruk jaye | Public Bot API ceiling | Bot parts me todta hai; ya local Bot API server |
| Disk full / `No space left` | `MAX_DOWNLOAD_GB` > disk | Disk 20 GB karo ya cap 10 GB |

---

## STEP 7 — Shell se self test (recommended)

Service → **Shell** tab:

```bash
python env_check.py     # env sane hai?
python selftest.py      # token, disk, ffmpeg, mux pipeline, Drive — sab
```

Sab ✅ = deployment perfect.

---

## STEP 8 — Update / redeploy

* GitHub par commit → Render auto-deploy
* Manual: **Manual Deploy** → *Deploy latest commit*
* Disk data safe rehta hai (ledger + resume files)

---

## Cost reality (Render)

| Item | Approx |
|---|---|
| Background Worker Starter | **$7/mo** |
| Disk 10 GB | $2.5/mo |
| Worker 1 vCPU/2 GB | $25/mo |
| Bandwidth | 25 GB included (Pro workspace), warna $0.15/GB |

Render **worker** par disk attach ho jata hai aur ownership issue entrypoint
solve karta hai — Railway se setup thoda simple hai, par per-instance fixed
price mehnga pad sakta hai.

---

**Railway par karna hai?** → [`RAILWAY_STEP_BY_STEP.md`](RAILWAY_STEP_BY_STEP.md)
