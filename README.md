# Google Drive → Telegram bot

> **Deploy karne aa gaye ho?** Seedha jao:
> · [`DEPLOY_WHICH_AND_HOW.md`](DEPLOY_WHICH_AND_HOW.md) — kaun sa host
> · [`RAILWAY_STEP_BY_STEP.md`](RAILWAY_STEP_BY_STEP.md) — Railway, har click
> · [`RENDER_STEP_BY_STEP.md`](RENDER_STEP_BY_STEP.md) — Render, har click
> · [`GOOGLE_OAUTH_PHONE_GUIDE.md`](GOOGLE_OAUTH_PHONE_GUIDE.md) — Google access (15 min)
>
> Deploy ke baad host shell me `python selftest.py` chalao — token, disk,
> ffmpeg, mux pipeline, Drive sab check ho jayega.


Fetches Google Drive files and re-uploads them to Telegram **without re-encoding**
(bit-for-bit identical to the source), with live progress, resumable downloads,
constant memory use, and automatic splitting for files bigger than Telegram's
upload limit.

```
requirements.txt      dependencies
gdrive_core.py        link parsing + recursive folder walker + resumable downloader
bot.py                the Telegram bot (python-telegram-bot v21, async, course queue)
oauth_setup.py        one-time helper that mints a Google refresh token
import_rclone_token.py  get the same credentials WITHOUT Google Cloud Console
test_core.py          21 tests: parsing, resume, confirm handshake, splitting
test_bot.py           18 tests: file_job, course queue, dedupe ledger, sequence order
railway_setup.py      volume/permission prep for Railway (run before bot.py)
healthcheck.py        optional HTTP liveness endpoint
railway.json          Railway deploy config
render.yaml           Render blueprint (worker + persistent disk)
Procfile / app.json   Heroku worker + one-click app definition
heroku_start.sh       Heroku container start command (ephemeral-disk aware)
deploy_gcp.sh         one-command Compute Engine (e2-micro) deploy
RAILWAY_DEPLOY.md     step-by-step Railway + Google setup guide
GCP_DEPLOY.md         step-by-step Google Cloud guide (VM / Cloud Run traps)
PAAS_DEPLOY.md        Render vs Railway vs Heroku, with step-by-step for each
GCP_KE_BINA.md        "can I avoid Google Cloud?" — hosting vs credentials, clearly
.env.example          configuration template
Dockerfile            container image
```

**39 offline tests, no Telegram token and no Google account needed to run them.**

---

## 1. What "auth" does and doesn't cover — read this first

The bot supports **two access modes**, both legitimate:

| Mode | What it can download | Setup |
|---|---|---|
| **Public** (default) | Anything the owner shared as *"anyone with the link"*. Files over 100 MB work via Google's standard virus-scan `confirm` handshake. | none |
| **Authorized** | **Everything your Google account can see**: your own Drive, files and folders shared with you or with the service account, shared drives, plus all public links. Private-by-default files included. | OAuth refresh token *or* service account JSON |

There is **no third mode**, deliberately.

- No harvesting of `cookies.txt` / `SID`, `HSID`, `SSID` session cookies.
- No attempts to make *someone else's* restricted file readable.
- No pretending that a 403 is a temporary glitch and hammering it with tokens.

If a file returns 404/403, the ACL says no for this bot's identity. Circumventing
that is credential abuse and a violation of Google's terms, and in practice it
doesn't even work for long — Google rotates session tokens and fingerprints
clients. The reliable, sanctioned path is the one shipped here: **authenticate as
the account that already has access.** For "my own locked/restricted file", that
fixes it in one step — authorize the bot, then just send the link.

> **Never paste your personal Drive cookies into a bot, this one included.** Use
> the OAuth flow below: it's revocable, scoped to read-only, and leaks nothing.

---

## 2. Install

```bash
git clone <your repo> && cd <your repo>
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Get a token from [@BotFather](https://t.me/BotFather), put it in `.env` as
`BOT_TOKEN=…`, then sanity-check without connecting:

```bash
python bot.py --check
python -m pytest -q          # 21 passing tests
python bot.py                # run
```

Restrict access — **do this before exposing the bot**:

```
ALLOWED_USER_IDS=123456789
```

(Your numeric id: [@userinfobot](https://t.me/userinfobot).) Without it anyone
who finds the bot can pull files through your credentials and burn your quota.

---

## 3. Authorizing the bot for private files

### Option A — OAuth refresh token (simplest)

1. [Google Cloud Console](https://console.cloud.google.com/) → new project.
2. **APIs & Services → Library → Google Drive API → Enable.**
3. **OAuth consent screen** → External → add your Google account as a *test user*.
4. **Credentials → Create credentials → OAuth client ID → Desktop app** →
   download the JSON as `client_secret.json` in this folder.
5. Run:

```bash
python oauth_setup.py
```

It opens a browser (or prints a URL for a machine with one), asks you to consent
to the read-only Drive scope, then prints:

```
GOOGLE_CLIENT_ID=…
GOOGLE_CLIENT_SECRET=…
GOOGLE_REFRESH_TOKEN=…
```

Paste those three lines into `.env`, restart the bot, and check with `/auth`.
The token is long-lived; it refreshes itself forever unless you revoke it at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions).

### Option B — service account (best for servers / Workspace)

1. Same console → **Credentials → Create credentials → Service account**.
2. Create a JSON key for it and save it as `service_account.json`.
3. Set `GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json`.
4. **Share the Drive folder/file with the service account's email**
   (looks like `name@project.iam.gserviceaccount.com`) — a service account has
   its own empty Drive, so sharing is what grants access.
5. Workspace only, if you need it to act as a user:
   `GOOGLE_DELEGATED_SUBJECT=you@yourdomain.com` plus domain-wide delegation.

### Option B2 — rclone token (skips Google Cloud Console entirely)

If you don't want to click through Cloud Console, rclone brings its own OAuth
client:

```bash
rclone config                # n -> gdrive -> drive -> scope drive.readonly -> auto config y
python import_rclone_token.py
```

Paste the three printed lines into `.env`. Same read-only grant, same revocation
page; the only trade-off is that rclone's client id is shared, so heavy use can
hit Google rate limits — then make your own with `oauth_setup.py`.

Full comparison (hosting vs credentials, with cost table):
[`GCP_KE_BINA.md`](GCP_KE_BINA.md).

### Option C — API key only

`GOOGLE_API_KEY=…` (restricted to the Drive API) gives accurate file sizes and
names for public links without any user credentials. Handy middle ground; still
can't touch private files.

---

## 4. Using it

Send any of these to the bot:

```
https://drive.google.com/file/d/<id>/view?usp=sharing
https://drive.google.com/open?id=<id>
https://drive.google.com/uc?export=download&id=<id>
https://docs.google.com/document/d/<id>/edit        → exported as PDF
https://docs.google.com/spreadsheets/d/<id>/edit    → exported as XLSX
https://docs.google.com/presentation/d/<id>/edit    → exported as PDF
https://drive.google.com/drive/folders/<id>         → first 30 files, one at a time
```

Up to 5 links per message. Commands: `/start`, `/auth`, `/cancel`, `/status`.

Uploads are lossless: video goes as `send_video` with `supports_streaming=True`
(no transcode), audio as `send_audio`, small images as `send_photo`, and
everything else as a document — Telegram stores documents byte-for-byte, so a
`.mkv`, `.zip` or `.iso` arrives intact. Google-native Docs are the one
exception: they have no binary form, so they're exported to PDF/XLSX.

---

## 4b. Course mode (folder → whole course on Telegram)

This is the mode for "I have access to a course, give me all of it".

1. Open the course folder in Drive (or the Drive folder your classroom/institute
   shared with you) and copy the link:
   `https://drive.google.com/drive/folders/<FOLDER_ID>`
2. Send it to the bot. It **scans inside subfolders** (Module 1, Module 2, …),
   resolves Drive shortcuts, and replies with a preview: file count, total size,
   first 8 names, and how many were already sent.
3. Press **▶️ Send all to Telegram**. From then on it works through the queue
   **one file at a time**: download → upload → delete temp → next.

What makes it survive a 300-video course:

| Feature | Behaviour |
|---|---|
| **Resume across restarts** | Every completed file id is written to a SQLite ledger (`JOBS_DB`). Bot crash, VPS reboot, network death — send the folder link again and it continues, skipping what you already have. |
| **No duplicate spam** | Same ledger. Press **♻️ Reset history** (or `/clear`) only when you actually want everything re-sent. |
| **Sequence guaranteed** | Depth-first walk (`FOLDER_ORDER=deep`): Module 1 ka *poora* content (`Lesson 1, 2, … 10`) pehle, phir Module 2. Natural sort — `Lesson 2` `Lesson 10` se pehle. Preview me numbered list, har message pe `[7/87]` counter. `FOLDER_ORDER=breadth` se level-by-level. |
| **Structure preserved** | `Module 1/Lesson 2/intro.mp4` arrives as `Module 1 - Lesson 2 - intro.mp4`, plus the full path in the caption. |
| **Google Docs inside the folder** | Exported automatically: Docs/Slides → PDF, Sheets → XLSX. |
| **Shortcuts** | `application/vnd.google-apps.shortcut` entries are resolved to their target file. |
| **Pagination** | Uses `nextPageToken`, so folders with >1000 children aren't truncated silently. |
| **Cloud-ready credentials** | `GOOGLE_SERVICE_ACCOUNT_JSON` (whole key in an env var) for hosts with no persistent disk, e.g. Railway. |
| **Bounded** | `MAX_FOLDER_FILES=300`, `MAX_DOWNLOAD_GB=20`, one course at a time, `/cancel` stops after the current file. |
| **Progress** | `/jobs` shows file N of M, sent/skipped/failed, elapsed time. |

Commands: `/jobs`, `/cancel`, `/clear`, `/status`, `/auth`.

Folder scanning needs **credentials or an API key** (Google's API won't list a
folder anonymously) — so this is where §3 authorization becomes mandatory.
Single public file links still work without it.

---

## 5. How it stays alive with multi-GB files

| Concern | Handling |
|---|---|
| **RAM** | Bodies are read with `iter_chunked(1 MiB)` and written straight to disk. Peak memory is flat regardless of file size — a 20 GB file uses the same ~1 MiB buffer as a 2 MB one. |
| **Crashes / drops** | Bytes go to `<name>.part`; the downloader sends `Range: bytes=N-` to resume from the last complete byte. Retries use exponential backoff (5 by default). |
| **Truncation** | Final size is compared against the Drive-reported size; a mismatch raises instead of silently uploading a broken file. |
| **Telegram's 2 GB cap** | Files over `MAX_UPLOAD_MB` (default 2000) are split into `.partNNN` chunks on disk and uploaded one by one, so nothing is held in memory twice. |
| **Upload progress** | A `TrackedReader` file wrapper counts bytes as Telegram's uploader pulls them; a 3-second watcher edits one message in place. |
| **Duplicate load** | `CONCURRENT_DOWNLOADS` semaphore (default 2) caps parallel transfers. |
| **Disk** | `MAX_DOWNLOAD_GB` refuses oversized files up front; temp files are deleted after upload (`KEEP_DOWNLOADED_FILES=false`). |
| **Timeouts** | Bot timeouts are raised to 1800 s for read/write so long uploads aren't killed mid-flight. |

For **>2 GB in a single Telegram message**, run a [local Bot API
server](https://github.com/tdlib/telegram-bot-api) and point
`BOT_API_BASE_URL=http://127.0.0.1:8081/bot`, then raise `MAX_UPLOAD_MB` (e.g.
2000000). Splitting is only needed on the public API.

---

## 6. Deploy

**Docker**

```bash
docker build -t gdrive-bot .
docker run -d --name gdrive-bot --restart unless-stopped \
  --env-file .env -v $PWD/service_account.json:/app/service_account.json:ro \
  -v gdrive-tmp:/tmp/gdrive-bot gdrive-bot
```

**systemd** (`/etc/systemd/system/gdrive-bot.service`)

```ini
[Unit]
Description=Google Drive to Telegram bot
After=network-online.target

[Service]
WorkingDirectory=/opt/gdrive-bot
EnvironmentFile=/opt/gdrive-bot/.env
ExecStart=/opt/gdrive-bot/.venv/bin/python bot.py
Restart=always
RestartSec=5
# keep the disk from filling up if something goes wrong
Nice=10
IOSchedulingClass=best-effort

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now gdrive-bot && journalctl -u gdrive-bot -f
```

Put `TEMP_DIR` on a volume with room for your `MAX_DOWNLOAD_GB`. On a 20 GB VPS,
set `MAX_DOWNLOAD_GB=8` and expect splits.

**Where to host it**

| Host | Guide | Notes |
|---|---|---|
| **Google Cloud VM** (cheapest) | [`GCP_DEPLOY.md`](GCP_DEPLOY.md) | `./deploy_gcp.sh` — e2-micro, 30 GB disk, systemd, ~5 min. Free-tier eligible in `us-central1/1/e`. Same project as your Drive API. |
| **Render** (best PaaS) | [`PAAS_DEPLOY.md`](PAAS_DEPLOY.md) §1 | `render.yaml` blueprint — background **worker** + **persistent disk**. ~$7/mo (no free plan with disks). |
| Railway | [`PAAS_DEPLOY.md`](PAAS_DEPLOY.md) §2 → [`RAILWAY_DEPLOY.md`](RAILWAY_DEPLOY.md) | Needs the `/data` volume, else the ledger resets each deploy. |
| Heroku | [`PAAS_DEPLOY.md`](PAAS_DEPLOY.md) §3 | Works, but **no persistent disk** → resume/dedupe lost on every restart. |
| Cloud Run (service) | ❌ not suitable | Idle scale-to-zero + 15 min request timeout + ephemeral disk kills polling bots and big uploads. Worker pool works but has no local disk. |
| VPS / Docker | below | `Dockerfile` + `docker run` |

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `404 — private and invisible to this bot` | File isn't shared with the bot's Google account. Authorize (§3) or ask the owner. |
| `403 (permission denied)` | Same; restricted to specific people. |
| `Sign in - Google Accounts` HTML | Private file, no credentials configured. Configure §3. |
| `quota exceeded` | Google's per-file download quota, typical on popular public links. Wait a few hours, or download as an authorized user / make your own copy. |
| `cannot scan / too large to scan` | Set `DRIVE_ACKNOWLEDGE_ABUSE=true` **only for files you own**. |
| Bot silent in groups | Privacy mode blocks non-command messages; make it an admin or use `/start@YourBot` first. |
| `Telegram rejected the upload` | Usually size — lower `MAX_UPLOAD_MB` or split; check `MAX_UPLOAD_MB ≤ 2000` on the public API. |
| Docs export fails | The file may be too large to export to Office; it falls back to PDF. |
| `Folder scanning needs Google credentials` | Expected: anonymous clients can't list Drive folders. Do §3, then `/auth` should show your account. |
| Course stops halfway | It's resumable — send the folder link again. Already-sent files are skipped via the ledger. |
| Nothing happens on "Send all" | Check `/jobs`; one course runs at a time, so yours may be queued behind another. |
| `Cannot list that folder (404)` | Folder isn't shared with the bot's Google account, or it's a shortcut to a folder the account can't see. |
| Want to re-send everything | `/clear` (or the ♻️ button) wipes this chat's ledger. |

---

## 8. Tests

```bash
pip install pytest aiohttp
python -m pytest -q
```

Covers link parsing across all URL shapes, filename sanitizing, Drive-native
export naming, the `confirm`-token handshake, resume-from-byte-offset, size
mismatch rejection, cancellation, the size guard, and split-and-reassemble
integrity (hash-compared). All local — the downloader is exercised against a
fake aiohttp Drive.

---

## 9. Legal / fair use

Use this for files you own or that were shared with you — a course you paid for
and whose access is tied to your Google account is exactly that case. It is not
a tool for getting into content you were not given access to: it refuses to
bypass access controls and ships no cookie harvester by design. Copying a paid
course into a Telegram channel to redistribute or resell it is copyright
infringement regardless of how the files were fetched, and it will get both the
bot and the account banned. Keep personal study copies personal, and check the
course provider's terms on offline copies.
