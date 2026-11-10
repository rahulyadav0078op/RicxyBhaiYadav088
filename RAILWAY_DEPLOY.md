> ⚠️ **Ye purana version hai.** Latest, screenshot-level guide: [`RAILWAY_STEP_BY_STEP.md`](RAILWAY_STEP_BY_STEP.md)

# Railway deploy — step by step (final)

Ye guide zero se live bot tak hai. Do hisse:

- **Part 1 — Google credentials** (ek hi baar, ~5 min) → private + restricted files ke liye
- **Part 2 — Railway deploy** (~10 min) → bot 24×7

Agar course ke files **"anyone with the link"** hain to Part 1 skip kar sakte ho
(aur baad me kabhi bhi add kar sakte ho — bot restart ke saath pick kar lega).

---

# Part 1 — Google credentials

> Ye tumhare Google account ka **read-only** access hai. Password kahin nahi
> jaata, scope `drive.readonly` hai, aur
> https://myaccount.google.com/permissions se kabhi bhi revoke kar sakte ho.

### 1.1 Drive API enable karo

1. https://console.cloud.google.com/ → project dropdown → **New project**
2. Naam: `gdrive-telegram` → **Create** → wahi project select karo
3. **APIs & Services → Library** → search `Google Drive API` → **Enable**

### 1.2 OAuth consent screen

1. **APIs & Services → OAuth consent screen**
2. **External** → **Create**
3. App name: `gdrive-bot`, support email: apna email → **Save and continue**
4. **Scopes → Add or remove scopes** → `.../auth/drive.readonly` tick → **Update**
   → **Save and continue**
5. **Test users → Add users** → **apna hi Gmail** → **Save**
6. (Recommended) **Publish app** dabao — warna refresh token **7 din** me expire
   ho jaata hai. Personal use me verification nahi lagta; "unverified" warning
   par *Advanced → Go to gdrive-bot* karke aage badhna.

### 1.3 OAuth client (Desktop app)

1. **Credentials → Create credentials → OAuth client ID**
2. Type: **Desktop app** → **Create** → **Download JSON**
3. File save karo `client_secret.json` naam se

### 1.4 Refresh token nikalo (laptop pe)

```bash
python oauth_setup.py
```

Browser khulega → apna Gmail → "unverified" warning pe **Advanced → Go to
gdrive-bot (unsafe)** → **Allow** (read-only hai, safe).

Terminal pe 3 lines aayengi — **inhe copy kar lo**, Part 2 me chahiye:

```
GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
GOOGLE_REFRESH_TOKEN=1//0gxxxxxxxxxxxxxxxx
```

> **Console nahi kholna?** `rclone config` (scope: drive.readonly) chalao, phir
> `python import_rclone_token.py` — wahi 3 lines mil jaayengi.
>
> **Service account chahiye?** Console → Service account → JSON key → uske email
> pe course folder **Viewer** share karo → Railway me `GOOGLE_SERVICE_ACCOUNT_JSON`
> me poora JSON ek line me paste karo.

### 1.4b Sirf phone hai? (laptop ke bina token nikalna)

`oauth_setup.py` laptop pe chalta hai (browser + Python ek hi machine pe). Agar
abhi sirf phone hai to **OAuth Playground** se token nikaal lo — poora kaam mobile
browser me ho jaata hai:

1. https://developers.google.com/oauthplayground kholo
2. Top-right **⚙️ gear icon** → **"Use your own OAuth credentials"** tick karo
3. Step 1.3 wala **Client ID** aur **Client secret** paste karo (Credentials page
   se copy karo — JSON download ki zaroorat nahi) → **Close**
4. Left panel me **Drive API v3** → `https://www.googleapis.com/auth/drive.readonly`
   tick karo → **Authorize APIs**
5. Apna Gmail chuno → "unverified" warning pe **Advanced → Go to gdrive-bot** →
   **Allow**
6. **Step 2: Exchange authorization code for tokens** dabao
7. Right side me JSON aayega — **`refresh_token`** copy kar lo. Wahi
   `GOOGLE_REFRESH_TOKEN` hai.

Playground tokens (apne client ke saath, app Published) revoke karne tak valid
rehte hain. Baad me laptop mil jaye to `oauth_setup.py` se dubara bana sakte ho —
dono ka output same hota hai.

> Playground wale page pe scope **drive.readonly** hi rakhna, `drive` (full) nahi.

### 1.5 Apna Telegram id note karo

[@userinfobot](https://t.me/userinfobot) ko message karo → wo tumhara numeric id
dega. `ALLOWED_USER_IDS` me yahi jaayega (warna koi bhi bot use kar sakta hai).

---

# Part 2 — Railway deploy

### 2.1 Code GitHub pe daalo

```bash
cd /path/to/project
git init
cat > .gitignore <<'EOF'
.env
client_secret.json
service_account.json
data/
*.sqlite3
__pycache__/
.pytest_cache/
EOF
git add .
git commit -m "gdrive telegram bot"
git branch -M main
git remote add origin https://github.com/<username>/gdrive-bot.git
git push -u origin main
```

> `.env`, `client_secret.json`, `service_account.json` **kabhi commit mat karo** —
> `.gitignore` ne block kiya hai. Verify: `git status --ignored | grep env`

### 2.2 Railway project + repo connect

1. https://railway.app → **Login with GitHub**
2. **New Project → Deploy from GitHub repo** → apna `gdrive-bot` select karo
3. Railway `railway.json` padhega aur `Dockerfile` se build karega
4. Pehla build ~2-3 min; **fail hoga** kyunki env vars abhi nahi hain — normal hai

### 2.3 Volume add karo — **ye step skip mat karna**

Bina volume ke har redeploy pe "kaun-kaun se file bheje" ledger aur adhure
`.part` downloads delete ho jaate hain → 12 GB course dobara file 1 se shuru.

1. Apni service kholo → **Variables** tab ke right me **+** → **Volumes**
   (ya service → **Data** → **Add Volume**)
2. **Mount path:** `/data`
3. Size: default theek hai
4. **Add**

> Bot pehli baar root se start hota hai, `/data` ka ownership theek karta hai,
> phir `botuser` (uid 1000) pe drop ho jaata hai — isliye
> `unable to open database file` wali classic error nahi aayegi.

### 2.4 Variables set karo

Service → **Variables** → **Raw Editor** (sabse fast) → ye paste karke apni
values bharo → **Update**:

```env
BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxx
ALLOWED_USER_IDS=123456789

# --- Google credentials (Part 1 se) ---
GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
GOOGLE_REFRESH_TOKEN=1//0gxxxxxxxxxxxxxxxx

# --- paths (volume ke andar) ---
DATA_DIR=/data
TEMP_DIR=/data/tmp
JOBS_DB=/data/jobs.sqlite3

# --- behaviour ---
FOLDER_ORDER=deep
FOLDER_RECURSIVE=true
MAX_FOLDER_FILES=300
MAX_DOWNLOAD_GB=15
MAX_UPLOAD_MB=2000
CONCURRENT_DOWNLOADS=1
KEEP_DOWNLOADED_FILES=false
LOG_LEVEL=INFO
```

**Service account use kar rahe ho?** OAuth ki 3 lines hata ke sirf ye rakho:

```env
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"...@....iam.gserviceaccount.com"}
```

Poora JSON **ek hi line** me, `\n` escapes ke saath. `railway_setup.py` validate
karta hai — galat JSON ho to logs me saaf error milega.

### 2.5 Deploy

Variables save karte hi Railway redeploy karega. Manually chahiye to **Deploy →
Redeploy**.

Logs (**Deployments → View Logs**) me ye dikhna chahiye:

```
• data dir   : /data (writable)
• jobs ledger: /data/jobs.sqlite3
• temp dir   : /data/tmp
Google credentials loaded (authorized mode).
Bot up as @your_bot (id 123456789)
```

Agar `No Google credentials or API key configured` dikhe → Variables me
credentials miss ya typo hai.

### 2.6 Verify (Telegram pe)

| Command | Expected |
|---|---|
| `/start` | Mode: **authorized** |
| `/auth` | Account: tumhara Gmail, Mode: OAuth user ✅ |
| `/status` | Disk free, temp dir `/data/tmp` |

`/auth` me error aaye → refresh token galat/expired (Part 1.4 dobara, aur app
**Publish** karna yaad rakho).

### 2.7 Course transfer

1. Course folder link bot ko bhejo:
   `https://drive.google.com/drive/folders/<FOLDER_ID>`
2. Bot preview dega — **numbered sequence**:
   ```
   1. Module 1/Lesson 1.mp4 — 240.5 MB
   2. Module 1/Lesson 2.mp4 — 198.0 MB
   ...
   Files: 87   Total size: 12.4 GB   Already sent: 0
   ```
3. **▶️ Send all to Telegram** dabao
4. Ek-ek karke sequence me aayega, live progress ke saath
5. `/jobs` progress · `/cancel` stop · `/clear` ledger reset

---

## Railway problems → fixes

| Error / symptom | Kyun | Fix |
|---|---|---|
| `unable to open database file` | volume root-owned tha | entrypoint fix kar deta hai; phir bhi aaye → volume `/data` pe confirm karo, aur **Settings → Deploy → Custom Start Command** = `/app/docker-entrypoint.sh` |
| Redeploy ke baad course file 1 se | volume nahi hai | 2.3 karo |
| `No Google credentials` | vars missing/typo | Raw Editor me dubara check; locally `python bot.py --check` |
| Bot reply nahi karta | `ALLOWED_USER_IDS` me id nahi / galat token | logs dekho; sahi id @userinfobot se |
| Build fail: `requirements.txt not found` | repo root se deploy nahi hua | `Dockerfile` repo ke root me hona chahiye |
| `/auth` fail after ~7 days | OAuth app Testing mode | 1.2 step 6: **Publish app** |
| `413` / bada video fail | 2 GB public API limit | bot khud split karta hai; ya local Bot API server (`BOT_API_BASE_URL`) |
| Disk full | temp files / bada course | `KEEP_DOWNLOADED_FILES=false`, `MAX_DOWNLOAD_GB` kam, volume size badhao |
| Bahut slow download | Railway egress + Google throttle | `CONCURRENT_DOWNLOADS=1` (default) |
| Crash loop | invalid JSON in service account var | `python bot.py --check` locally |
| Duplicate files | do replica chal rahe | `numReplicas=1` (railway.json me set hai) |
| Cloud Hub pe `Write access to project ... check billing account` | Cloud Hub dashboard ka apna error; Drive API/OAuth ko billing nahi chahiye | **Ignore karo** — `APIs & Services → Enabled APIs` me `Google Drive API` dikhna kaafi hai |

---

## Cost (Railway)

- Trial ke baad **Hobby ~$5/month** + usage (vCPU/RAM/egress).
- 12 GB course ≈ 12 GB download egress + 12 GB Telegram upload → pehli baar
  usage spike dikhega, phir normal.
- Volume ka charge usage ke hisaab se chhota hota hai.
- Paisa bachana hai → `MAX_DOWNLOAD_GB` kam, `CONCURRENT_DOWNLOADS=1`, aur ek hi
  baar course bhejo (`/clear` sirf tab jab dobara bhejna ho).

---

## Deploy ke baad — updates

```bash
git add . && git commit -m "tweak" && git push    # Railway auto-deploy karega
```

Ledger aur downloads volume pe hain, to redeploy se resume nahi tootega.

---

## Ek line me

`git push` → Railway → volume `/data` → 12 variables → deploy → `/auth` →
folder link → **▶️ Send all**. Bas.

Legal note (repeat kar raha hoon, zaroori hai): course tumne kharida ya tumhare
account se shared hai — us personal copy ko Telegram pe rakhna theek, par public
channel pe redistribute/resell karna copyright infringement hai aur bot/account
ban ho sakta hai. Bot access-control todne ki koshish nahi karta; jo tumhare
account ko dikhta hai wahi deta hai.
