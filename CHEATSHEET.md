# Cheatsheet — bot kya karta hai, kaise use karna hai

## One line
Google Drive link bhejo → bot download karke Telegram pe **original quality** me bhej deta hai.
Course folder bhejo → **poora course sequence me** (Module 1 → Lesson 1, 2, 3… → Module 2).

---

## Kya kar sakta hai

| Capability | Detail |
|---|---|
| **Single file** | `drive.google.com/file/d/<id>/view`, `/open?id=`, `/uc?id=` |
| **Google Docs / Sheets / Slides** | PDF / XLSX me auto-export |
| **Folder = course mode** | Subfolders tak scan, numbered preview, ek-ek karke sequence me |
| **Private + restricted files** | Tumhare Google account ko jo dikhta hai — wahi milta hai (OAuth read-only) |
| **Public links** | Bina credentials bhi public files chalti hain |
| **Resume** | Net gaya / bot restart — jahan ruka tha wahin se aage |
| **Dedupe** | Wahi folder dobara bhejo → sirf baaki files (ledger volume pe) |
| **Bada file** | 2 GB se bada = auto split, phir `cat part* > file` |
| **Lossless** | Video `send_video` (no transcode), PDF/zip document — byte-for-byte |
| **Live progress** | %, speed, ETA; `/cancel` se rok |
| **Ek message ke 5 links** | 5 tak links ek saath |
| **Folder listing** | Scan karke list dikha sakta hai (download ke bina) |

## Kya nahi kar sakta (by design)

- Kisi **aur** ke private/locked course ka access todna — sirf tumhare account ki permission
- Video ka **transcode/re-encode** (quality change nahi karta, isliye lossless hai)
- YouTube/paid platforms ke DRM content
- 2 GB se bada file **ek hi message** me (public Bot API limit → split hota hai)
- Telegram pe **upload ke time** 2 GB+ chahiye → local Bot API server chahiye

---

## Kaise use karna hai

### Step 0 — setup (ek hi baar)
Credentials chahiye (`BOT_TOKEN`, `ALLOWED_USER_IDS`, aur private files ke liye 3 Google values).
Poora guide: `PHONE_CHECKLIST.md` (phone) ya `RAILWAY_DEPLOY.md` (laptop/Railway).

Verify: bot ko `/auth` bhejo → tumhara Gmail dikhe = ready.

### Step 1 — single file
```
https://drive.google.com/file/d/1AbCdEf.../view
```
Bot: download → upload → done.

### Step 2 — poora course (folder)
```
https://drive.google.com/drive/folders/1OoGb2u2v9o9...
```
1. Bot scan karega (subfolders included)
2. **Numbered preview** aayega:
   ```
   Files: 87   Total: 12.4 GB   Already sent: 0
   1. Module 1/Lesson 1.mp4 — 240.5 MB
   2. Module 1/Lesson 2.mp4 — 198.0 MB
   ...
   ```
3. **▶️ Send all to Telegram** dabao
4. Ek-ek karke sequence me aayega — `[7/87]` counter ke saath
5. `/jobs` = progress · `/cancel` = stop · `/clear` = ledger reset

---

## Commands

| Command | Kaam |
|---|---|
| `/start` | Mode + supported links |
| `/auth` | Kaunsa Google account use ho raha hai (private files ke liye zaroori) |
| `/jobs` | File N of M, sent / skipped / failed |
| `/cancel` | Chal raha transfer roko (resume ho sakta hai) |
| `/clear` | Ledger reset → agli baar sab dobara bhejega |
| `/status` | Disk free, temp dir, parallel downloads |

---

## Sequence kaise decide hota hai

```
1. Module 1/Lesson 1.mp4     ← Module 1 poora pehle
2. Module 1/Lesson 2.mp4
3. Module 1/Lesson 10.mp4    ← natural sort (2 se pehle 10 nahi)
4. Module 2/…
5. Module 10/…               ← "Module 10" "Module 2" ke baad
6. README.pdf                ← loose files last
```

Badalna hai? `FOLDER_ORDER=deep` (default) ya `breadth` (level by level).

---

## Settings jo tune kar sakte ho

| Variable | Default | Matlab |
|---|---|---|
| `MAX_DOWNLOAD_GB` | 20 (Railway: 15) | isse bada file skip |
| `MAX_UPLOAD_MB` | 2000 | Telegram limit; bada = split |
| `MAX_FOLDER_FILES` | 300 | ek folder scan me max |
| `CONCURRENT_DOWNLOADS` | 2 (Railway: 1) | parallel transfers |
| `FOLDER_ORDER` | deep | sequence order |
| `FOLDER_RECURSIVE` | true | subfolders include |
| `KEEP_DOWNLOADED_FILES` | false | upload ke baad temp delete |

---

## Abhi tumhara status

| Cheez | Status |
|---|---|
| Bot Railway pe chal raha hai | ✅ (screenshot me reply kar raha tha) |
| Folder link parse + scan trigger | ✅ |
| Google credentials | ❌ `invalid_client` — refresh token dusre client ka hai |
| Fix | Playground ⚙ me **apna** Client ID/Secret daalo → naya token → Railway variables update → `/auth` |

Uske baad course folder link bhejo → **▶️ Send all** → kaam shuru.
