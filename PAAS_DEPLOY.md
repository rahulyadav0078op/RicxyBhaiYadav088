# Render / Heroku / Railway — kaun sa, aur kaise

Seedha jawaab: **teeno pe deploy ho sakta hai**, par is bot ke liye ranking hai.

| | **Render** | **Railway** | **Heroku** |
|---|---|---|---|
| Bot 24×7 chalta hai | ✅ worker service | ✅ | ✅ worker dyno |
| **Persistent disk** (resume ledger + `.part` files) | ✅ **built-in, 10 GB tak easy** | ✅ volume (`/data`) | ❌ **nahi** — ephemeral, har restart pe ledger reset |
| Setup effort | ⭐ easy (`render.yaml` blueprint) | easy | medium (container stack quirks) |
| Free tier? | ❌ worker + disk = **paid**, ~$7/mo + $0.25/GB disk | ❌ trial ke baad ~$5/mo | ❌ koi free nahi, ~$7/dyno |
| Long uploads (2 GB) safe? | ✅ background worker, koi request timeout nahi | ✅ | ✅ worker dyno |
| Idle pe sleep? | ❌ nahi (worker) | ❌ nahi | ❌ nahi (worker, par free hours nahi hain) |
| **Verdict** | **✅ best PaaS** | ✅ theek | ⚠️ chalega, par resume/dedupe nahi milega |

**Recommendation:** Render (worker + disk) > Railway (volume) > Heroku.
Aur agar tum Google Cloud pe already ho to `GCP_DEPLOY.md` wala **GCE VM sabse
sasta aur sabse control me** hai — PaaS pe tum ₹600–800/month doge jo VM pe
free/₹600 me ho jaata hai, plus disk ka jhanjhat nahi.

> ⚠️ **Teeno pe ek hi rule:** web service mat banana. Ye bot **outbound** polling
> karta hai, koi HTTP request nahi aati. Web service idle pe so jaayegi aur 15–30 s
> request timeout par bada video upload beach me mar jaayega. **Worker/background
> service** hi banao — teeno files me already wahi set hai.

---

## 1. Render — step by step (recommended)

`render.yaml` ready hai, to blueprint se ek click me ban jaata hai.

### 1.1 Code GitHub pe

```bash
git add .
git commit -m "render blueprint"
git push
```

(Pehle `GCP_DEPLOY.md` / `RAILWAY_DEPLOY.md` Phase A wala Google OAuth setup kar
lo — `GOOGLE_REFRESH_TOKEN` chahiye.)

### 1.2 Blueprint deploy

1. https://dashboard.render.com → **New → Blueprint**
2. Apna repo select karo → Render `render.yaml` padh lega
3. **Apply** dabao
4. Render poochhega `sync: false` wale secrets — yahan bharo:
   - `BOT_TOKEN` — @BotFather se
   - `ALLOWED_USER_IDS` — apna Telegram id (@userinfobot)
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`
     (ya sirf `GOOGLE_SERVICE_ACCOUNT_JSON` — poora JSON ek line me)
5. **Deploy** → ~3-4 min

### 1.3 Verify

Render dashboard → service → **Logs**. Ye dikhna chahiye:

```
• data dir   : /data (writable)
• jobs ledger: /data/jobs.sqlite3
Google credentials loaded (authorized mode).
Bot up as @your_bot (id …)
```

Telegram pe `/auth` → tumhara Gmail → ready.

### 1.4 Course transfer

Folder link bhejo → numbered preview → **▶️ Send all to Telegram**.
Ledger disk pe hai, to redeploy ke baad bhi resume kaam karega —
`/clear` se hi reset hoga.

### 1.5 Zyada disk / settings

Dashboard → service → **Settings**:
- Disk size: 10 GB default (course bada hai to 20 GB)
- `MAX_DOWNLOAD_GB` disk size se **kam** rakho (default 15 GB — theek hai)
- **numInstances = 1 rakho.** Do instance = do poller = duplicate uploads.

> Render free plan pe **disk nahi milta** aur web service idle pe so jaati hai —
> isliye `plan: starter` set kiya hai. Ye ~$7/month + disk ka thoda charge.

---

## 2. Railway — step by step

Poora guide `RAILWAY_DEPLOY.md` me hai, short version:

1. GitHub repo → https://railway.app → **New Project → Deploy from GitHub**
2. Service → **+ → Volume** → mount path **`/data`** (ye step skip mat karna)
3. **Variables → Raw Editor** → `RAILWAY_DEPLOY.md` B4 wali list paste karo
4. Redeploy → logs me `Bot up as @…` → Telegram pe `/auth`

`railway.json` already start command set karta hai
(`python railway_setup.py && python bot.py`), to volume root-owned hone wali
classic error nahi aayegi.

---

## 3. Heroku — chalega, par ek limitation hai

`Procfile` (worker) + `Dockerfile` + `app.json` ready hain.

### 3.1 Shuru karo

```bash
heroku login
heroku create your-gdrive-bot
heroku stack:set container            # Dockerfile se build hoga
heroku ps:scale web=0 worker=1        # sirf worker chale
```

### 3.2 Config vars

```bash
heroku config:set BOT_TOKEN="123456:ABC..." \
  ALLOWED_USER_IDS="123456789" \
  GOOGLE_CLIENT_ID="..." \
  GOOGLE_CLIENT_SECRET="..." \
  GOOGLE_REFRESH_TOKEN="..." \
  FOLDER_ORDER="deep" \
  MAX_DOWNLOAD_GB="15" \
  CONCURRENT_DOWNLOADS="1" \
  DATA_DIR="/app/data" \
  TEMP_DIR="/app/data/tmp" \
  JOBS_DB="/app/data/jobs.sqlite3"
```

### 3.3 Deploy

```bash
git push heroku main
heroku logs --tail -a your-gdrive-bot
```

### 3.4 Heroku ki asli dikkat

Heroku ka filesystem **ephemeral** hai — koi persistent volume nahi. Matlab:

| Kaam karta hai | Nahi karta |
|---|---|
| Video/PDF download + Telegram upload | Restart ke baad "kaun-kaun bheja" ledger yaad nahi |
| Sequence order, progress, /cancel | Adhura `.part` file resume nahi hoga (dubara shuru) |
| Private + public files (OAuth ke saath) | Bade course me dobara restart = duplicate uploads |

Redeploy ya dyno restart (Heroku din me kam se kam ek baar cycle karta hai) par
ledger khaali ho jaata hai. `heroku_start.sh` isko `$HOME/data` me rakh ke warn
karta hai. Clean fix chahiye to:

- **Render/GCE** use karo (disk hai), ya
- Heroku + **Postgres** me ledger shift karo — bolo to main `JobStore` me
  Postgres backend add kar dunga (chhota kaam hai).

---

## Kaunsa chunu — 10 second decision

```
Course ek hi baar bhejna hai, restart ki tension nahi?        → Heroku / koi bhi
Restart safe resume + dedupe chahiye, minimal jhanjhat?       → Render (worker+disk)
Google Cloud already use kar rahe ho, sasta chahiye?          → GCE VM (deploy_gcp.sh)
Railway pe already account hai?                               → Railway + /data volume
```

---

## Common traps (teeno pe same)

| Symptom | Fix |
|---|---|
| Bot message ka reply nahi karta | Web service bana diya → **worker** banao |
| Deploy ke baad course file 1 se shuru | Disk/volume mount nahi hai → Render disk ya Railway volume |
| Duplicate files do baar aa rahe | `numInstances=1` / ek dyno; ya `/clear` se ledger reset |
| `413 Request Entity Too Large` | platform limit; 2 GB se bada file split hoga (bot khud karta hai) |
| Logs me `No Google credentials` | OAuth vars missing ya JSON galat — `python bot.py --check` locally chalao |
| Build fail: `requirements.txt not found` | repo root se deploy kar rahe ho? Dockerfile root me hona chahiye |
| 7 din baad `/auth` fail | OAuth consent app **Testing** mode me hai → **Publish app** karo |
| Paisa khatam ho raha | `MAX_DOWNLOAD_GB` kam karo, `CONCURRENT_DOWNLOADS=1`, aur `KEEP_DOWNLOADED_FILES=false` |
