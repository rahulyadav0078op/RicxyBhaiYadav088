# Deploy — kaun sa host, aur kaise (short version)

Do poore step-by-step guides hain:

| Guide | Host | Kiske liye |
|---|---|---|
| [`RAILWAY_STEP_BY_STEP.md`](RAILWAY_STEP_BY_STEP.md) | Railway | **Recommended** — sasta, usage-based, Singapore region, disk easy |
| [`RENDER_STEP_BY_STEP.md`](RENDER_STEP_BY_STEP.md) | Render | Fixed price chahiye / blueprint (repo se auto-setup) pasand ho |

Pehle ek chhoti si cheez: **Google access** —
[`GOOGLE_OAUTH_PHONE_GUIDE.md`](GOOGLE_OAUTH_PHONE_GUIDE.md) (15 min, phone se).

---

## 5-line summary (dono hosts par same)

```
1. gdrive-telegram-bot.zip  →  GitHub private repo me upload
2. Host par naya service    →  Docker runtime, Dockerfile root me
3. /data par persistent disk attach  (10 GB)
4. Variables: BOT_TOKEN, ALLOWED_USER_IDS, GOOGLE_* , DATA_DIR=/data, JOBS_DB=/data/jobs.sqlite3
5. Deploy → logs me "Bot up as @…" → Telegram me /doctor
```

---

## Railway vs Render (Nov 2026)

| | Railway | Render |
|---|---|---|
| Plan | Hobby **$5/mo** ($5 usage included) | Hobby $0 + per-instance |
| Worker | koi bhi service | Background Worker **$7/mo** se |
| Disk | $0.15/GB-mo | $0.25/GB-mo |
| Singapore | ✅ `asia-southeast1` | ✅ |
| Setup | GitHub → Docker builder → Volume | Blueprint (`render.yaml`) ya manual worker |
| Best for | kam-budget, variable load | fixed predictable bill, blueprint |

Dono par **1 replica** hi rakhna — do pollers duplicate upload karte hain.

---

## Deploy se pehle 60-second sanity

Zip ke andar `python env_check.py` chalao (ya host ke shell me):

```
✓ BOT_TOKEN looks valid
✓ ALLOWED_USER_IDS = 987654321
✓ OAuth credentials complete
✓ Resume ledger inside the persistent disk
✗ koi problem → wahi fix karo, phir deploy
```

Deploy ke baad:

```
python selftest.py      # token, disk, ffmpeg, mux pipeline, Drive — sab check
```

---

## Features jo deploy ke baad turant milte hain

| Command | Kaam |
|---|---|
| link bhejo | koi bhi public/drive file → Telegram, original quality |
| folder link bhejo | poora course, course sequence me, resume ke saath |
| `/streamdl <link>` | view-only video: video track + audio track alag → lossless join → send |
| `/merge` | chat me maujood video + audio tracks ko jodna |
| `/quotafix <link>` | "download quota exceeded" → copy → download → delete |
| `/auth` `/doctor` `/status` `/jobs` `/cancel` `/clear` `/fixwebhook` | ho gaya / diagnose |

`AUTO_QUOTA_FIX=true` (+ `DRIVE_SCOPE=full`) karo to quota wall khud handle ho
jati hai — `/quotafix` type karne ki zaroorat nahi.
