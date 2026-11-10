# START HERE (ZIP ke andar)

> **Sirf deploy karna hai?** → [`DEPLOY_WHICH_AND_HOW.md`](DEPLOY_WHICH_AND_HOW.md)
> **Railway:** [`RAILWAY_STEP_BY_STEP.md`](RAILWAY_STEP_BY_STEP.md) ·
> **Render:** [`RENDER_STEP_BY_STEP.md`](RENDER_STEP_BY_STEP.md) ·
> **Google access:** [`GOOGLE_OAUTH_PHONE_GUIDE.md`](GOOGLE_OAUTH_PHONE_GUIDE.md)
>
> **Shuru se poora roadmap:** `FULL_JOURNEY.md` — Stage 0 se 6 tak, time ke saath.

Ye archive: Google Drive → Telegram bot (private + public files, course mode,
view-only video pipeline).

## 1. Kya chahiye
- `BOT_TOKEN` — Telegram pe @BotFather → `/newbot`
- `ALLOWED_USER_IDS` — @userinfobot se apna numeric id
- Google creds (private files ke liye): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`
  → 15-minute phone guide: **`GOOGLE_OAUTH_PHONE_GUIDE.md`**

## 2. Deploy se pehle (60 second)
```bash
python env_check.py          # token shape, user ids, Google keys, disk, ffmpeg
python bot.py --check        # config import graph verify
python -m pytest -q          # 96 offline tests
```

## 3. Deploy (do poore guides)
- **Railway** → `RAILWAY_STEP_BY_STEP.md` (recommended, ~$5/mo)
- **Render** → `RENDER_STEP_BY_STEP.md` (blueprint ya manual worker)

Dono par ek hi cheez zaroori: **`/data` par persistent disk** + **1 replica**.

## 4. Deploy ke baad (host ke shell me)
```bash
python selftest.py           # token, disk, ledger, ffmpeg, mux pipeline, Drive — sab check
```
Sab ✅ aaye to Telegram me `/doctor` bhi ✅ aayega.

## 5. Bot commands
| Command | Kaam |
|---|---|
| link bhejo | file → Telegram, original quality (no re-compress) |
| folder link bhejo | poora course, course sequence me, resume ke saath |
| `/streamdl <link>` | **view-only video**: video track + audio track alag download → lossless join → send |
| `/merge` | chat me maujood video + audio tracks ko jodna |
| `/quotafix <link>` | "download quota exceeded" → copy → download → delete |
| `/auth` | kaunsa Google account connected hai |
| `/doctor` | poora self-check (keys, token, Drive, disk, ffmpeg) |
| `/jobs` `/status` `/cancel` `/clear` `/fixwebhook` | progress, queue, stop, history reset, 409 fix |

`AUTO_QUOTA_FIX=true` + `DRIVE_SCOPE=full` → quota wall khud handle.

## 6. Files
| File | Kya |
|---|---|
| `bot.py` | Telegram bot (course queue, progress, sequence, /streamdl, /merge, /quotafix) |
| `gdrive_core.py` | Drive link parsing, recursive folder walk, resumable downloader, stream-part scraper |
| `media_download.py` | ek track (video-only ya audio-only) ka resumable download + stale-link detection |
| `media_mux.py` | ffmpeg stream-copy join + post-merge verification (silent audio loss pakadta hai) |
| `oauth_helper.py` | refresh token banane ka helper (local server ya `--manual`) |
| `oauth_setup.py` | purana helper (google-auth-oauthlib par based) |
| `env_check.py` | deploy se pehle env validation (galat token, @username, disk… ) |
| `selftest.py` | host par poora functional self test (mux pipeline bhi) |
| `import_rclone_token.py` | creds bina Google Cloud Console (rclone se) |
| `Dockerfile`, `docker-entrypoint.sh`, `railway.json`, `render.yaml` | deploy |
| `railway_setup.py`, `healthcheck.py`, `health.py`, `doctor.py` | volume prep + health |
| `test_*.py` | 96 offline tests (ffmpeg wale real-binary tests auto-skip) |
| `RAILWAY_STEP_BY_STEP.md`, `RENDER_STEP_BY_STEP.md` | deploy guides |
| `GOOGLE_OAUTH_PHONE_GUIDE.md` | phone se OAuth, har click |
| `VIDEO_TECHNIQUE_ANALYSIS.md` | "view-only video" trick ka technical breakdown |
| `SECURITY_AND_QUOTA.md`, `AUDIT_REPORT.md`, `RENDER_DEEP_DIVE.md` | safety + quota + Render detail |
| `README.md` | poora documentation |

## 7. Zaroori baatein
- `.env`, `client_secret.json`, `service_account.json` **kabhi git pe push mat karna** (`.gitignore` me blocked hai).
- `ALLOWED_USER_IDS` khaali chhoda to koi bhi bot use kar sakta hai — bharna zaroori.
- Bot sirf wahi files deta hai jo tumhare Google account ko dikhti hain — access control bypass nahi karta. **Google password bot kabhi nahi maangta.**
- Paid course ka personal copy rakhna tumhara call; public channel pe redistribute karna copyright issue + ban risk hai.
