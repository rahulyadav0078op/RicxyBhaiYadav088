# Poora safar — shuru se aakhir tak (ek hi doc)

Ye tumhara "roadmap" hai. Har stage: **kya hoga, kaun karega, kitna time, output kya**.
Neeche se shuru mat karo — **Stage 0 padho, phir 1, 2, 3…** order me.

---

## Stage 0 — Pehle ye samajh lo (5 min padho, 0 kaam)

**Ban kya raha hai:** Google Drive → Telegram transfer bot.
- Drive link bhejo → file Telegram pe, original quality
- Course folder link bhejo → poora course sequence me (Module 1 → Lesson 1,2,3… → Module 2)

**Kaam kaise karta hai:** bot tumhare **Google account** se read-only permission leta hai,
aur jo files us account ko dikhti hain wo Telegram pe bhej deta hai.

**Do hadd (pehle se clear):**
1. Kisi **aur** ka private/locked course nahi khulega — sirf tumhare account ki access
2. Bot me **email/password** nahi daalte — Google ka official "Allow" button (OAuth), jo
   kabhi bhi revoke ho sakta hai

---

## Stage 1 — Accounts ready karo (10 min)

| Chahiye | Kaam | Free? |
|---|---|---|
| **Telegram** | account + @BotFather access | ✅ |
| **Google account** | wahi jisme course ka access hai | ✅ |
| **GitHub** | code rakhne ke liye (Render wahan se padhta hai) | ✅ |
| **Render** | hosting (worker + disk) | ❌ ~$9.5/month (card chahiye) |

> Card nahi dena chahte? **Laptop pe chalao** — `python bot.py`, bilkul free,
> bas course transfer ke dauraan laptop chalta rehna chahiye.

---

## Stage 2 — 3 credentials banao (15 min) ← sabse zyada atakne wala hissa

### 2a. `BOT_TOKEN` (2 min)
Telegram → **@BotFather** → `/newbot` → naam + username → token milega
`123456789:AAH...`

### 2b. `ALLOWED_USER_IDS` (1 min)
Telegram → **@userinfobot** → message karo → wo tumhara numeric id dega
`123456789`

### 2c. Google credentials — 3 values (10 min, 3 raaste)

| Raasta | Console chahiye? | Kab chuno |
|---|---|---|
| **A. rclone** (`setup_wizard.py --path rclone`) | ❌ nahi | **Default — sabse aasan** |
| **B. apna OAuth client** | ✅ 5 min | Apna quota chahiye / rclone rate-limit ho |
| **C. public only** | ❌ | Course ke files "anyone with the link" hain |

Raasta A me:
```bash
python setup_wizard.py --path rclone
```
→ browser me apna Gmail → **Allow** → wizard khud 3 values nikaal ke `.env` me likh deta hai
aur Google se test karke batata hai chal raha hai ya nahi.

**Output:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`

> Teen values **ek hi client** ki honi chahiye. Yahi wo galti hai jisse
> `invalid_client` aata hai.

---

## Stage 3 — Code GitHub pe (5 min)

1. `gdrive-telegram-bot.zip` download → extract
2. https://github.com/new → name `gdrive-bot` → **Private** → Create
3. **Add file → Upload files** → `gdrive-bot/` folder ki saari files → Commit
4. Verify: root me `Dockerfile`, `requirements.txt`, `bot.py` dikhe

⚠️ `.env` upload **nahi** karna (credentials). `.env.example` karna hai.

---

## Stage 4 — Render pe deploy (10 min)

1. dashboard.render.com → **New + → Blueprint** → repo select → **Apply**
   *(ya manual: New + → **Worker** → Docker → Starter plan)*
2. **Disk add karo** → Settings → Disks → Mount Path **`/data`** → 10 GB
   ⚠️ Iske bina har redeploy pe resume ledger delete → course file 1 se shuru
3. **Environment** me Stage 2 ki values + ye paths:

```env
BOT_TOKEN=...
ALLOWED_USER_IDS=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=1//...
DATA_DIR=/data
TEMP_DIR=/data/tmp
JOBS_DB=/data/jobs.sqlite3
FOLDER_ORDER=deep
MAX_DOWNLOAD_GB=15
CONCURRENT_DOWNLOADS=1
```

4. **Save** → deploy 3–5 min → logs me `Bot up as @your_bot`

---

## Stage 5 — Verify (2 min, Telegram me hi)

| Command | Expected |
|---|---|
| `/start` | bot zinda hai |
| `/auth` | tumhara Gmail dikhe ✅ |
| **`/doctor`** | 10 checks — sab ✅ |

`/doctor` ka faayda: shell/dashboard me jaane ki zaroorat nahi, bot khud batata hai
kya galat hai (token, client match, folder access, webhook, disk).

Koi ❌ aaye → `DEPLOY_STEP_BY_STEP.md` ki error table.

---

## Stage 6 — Course transfer (as long as it takes)

1. Folder link bhejo: `https://drive.google.com/drive/folders/<FOLDER_ID>`
2. **Numbered preview** aayega — kitni files, kitna size, sequence
3. **▶️ Send all to Telegram**
4. Ek-ek karke sequence me aayega (`[7/87]` counter)
5. `/jobs` progress · `/cancel` stop · `/clear` ledger reset

Restart ho gaya beech me? Wahi folder link dobara bhejo → jahan ruka tha wahin se.

---

## Poora safar ek line me

```
Accounts (10m) → 3 credentials (15m) → GitHub upload (5m) → Render + disk + env (10m)
→ /doctor ✅ → folder link → ▶️ Send all
```
**Total: ~40 minute + course transfer time.**

---

## Kabhi bhi atko to

| Step | Tool |
|---|---|
| Credentials galat | `python setup_wizard.py --check-only` ya `python diagnose_credentials.py` |
| Bot chup hai | chat me `/fixwebhook`, phir `/doctor` |
| Deploy crash | Render → Logs → last 30 lines |
| Kuch bhi samajh na aaye | `CHEATSHEET.md` (bot kya kar sakta hai) + `GCP_KE_BINA.md` (Google Cloud zaroori hai kya) |

---

## Tum abhi kahan ho

| Stage | Status |
|---|---|
| 0. Samajh | ✅ |
| 1. Accounts | ✅ (Render bhi) |
| 2a. BOT_TOKEN | ✅ |
| 2b. ALLOWED_USER_IDS | ✅ |
| **2c. Google credentials** | ❌ **yahin atke ho** — `invalid_client` |
| 3. GitHub | ✅ (deploy hua) |
| 4. Render + disk | ✅ |
| 5. `/doctor` | pending |
| 6. Course | pending |

**Ab sirf ek kaam:** Stage 2c — `setup_wizard.py --path rclone` chalao (ya Playground me
apna client), teen values ek saath update karo, Redeploy, phir `/doctor`.
