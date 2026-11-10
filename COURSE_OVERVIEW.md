# Course transfer — 5 minute overview

Bot ka kaam: tumhare Google account ko jo Drive files dikhti hain (course ke
videos, PDFs, notes), unhe **sequence me** Telegram pe bhej dena — bina quality
loss, bina dobara upload kiye.

---

## Flow (ek nazar me)

```
Google Drive (course folder)
        │  folder link bot ko bhejo
        ▼
   ┌─────────────┐   1. scan (subfolders bhi)
   │   SCAN      │   2. sorted list banao: Module 1 → Lesson 1,2,3…10 → Module 2
   └─────────────┘
        │  numbered preview + "Send all" button
        ▼
   ┌─────────────┐   har file ke liye:
   │   QUEUE     │   3. download (resumable, 1 MB chunks)
   │  one by one │   4. upload to Telegram (original quality)
   └─────────────┘   5. temp file delete + ledger me "sent" mark
        │
        ▼
   Telegram chat: 1/87 … 2/87 … 3/87 … ✅ Course finished
```

---

## Sequence kaise decide hota hai (`FOLDER_ORDER=deep`)

```
1.  Module 1/Lesson 1.mp4      ← Module 1 poora pehle
2.  Module 1/Lesson 2.mp4
3.  Module 1/Lesson 10.mp4     ← natural sort (2 se pehle 10 nahi)
4.  Module 2/…
5.  Module 10/…                ← "Module 10" "Module 2" ke baad
6.  README.pdf                 ← loose files last
```

`FOLDER_ORDER=breadth` chahiye to level-by-level jayega.

---

## File types kaise bheje jaate hain

| Type | Kaise | Quality |
|---|---|---|
| `.mp4`, `.mkv`, `.mov`, `.webm` | `send_video` + streaming | **No transcode** — original bytes |
| `.mp3`, `.m4a`, `.opus`, `.flac` | `send_audio` | original |
| `.jpg`, `.png` (9 MB se chhota) | `send_photo` | original |
| `.pdf`, `.zip`, `.docx`, `.iso`, baaki sab | `send_document` | original, byte-for-byte |
| Google Docs | PDF export | converted (Google Doc ka binary form nahi hota) |
| Google Sheets | XLSX export | converted |
| Google Slides | PDF export | converted |

Telegram documents ko **kabhi** re-encode nahi karta, isliye PDF/zip/video
original hi aate hain.

---

## Bade course ke liye 5 safety features

1. **RAM flat** — file 2 MB ho ya 20 GB, memory ~1 MB hi rehti hai (1 MB chunks disk pe likhte hain)
2. **Resume** — `<name>.part` + `Range: bytes=N-`; net toota ya bot restart hua, wahi se aage
3. **Dedupe ledger** — SQLite me "kaun-kaun bheja" (volume pe). Folder link dobara bhejo → sirf baaki files
4. **Auto split** — 2 GB se bada file Telegram limit ke hisaab se `.partNNN` me toot ke jaata hai
5. **Ek waqt me ek file** — `/cancel` se current file ke baad stop, disk full nahi hota

---

## Commands

| Command | Kya karta hai |
|---|---|
| `/start` | Mode + supported links |
| `/auth` | Kaunsa Google account use ho raha hai (private files ke liye zaroori) |
| `/jobs` | File N of M, sent/skipped/failed, elapsed |
| `/cancel` | Transfer rok do (resume ho sakta hai) |
| `/clear` | Ledger reset — agli baar sab dobara bhejega |
| `/status` | Disk free, temp dir, parallel downloads |

---

## Numbers jo pata hone chahiye

| Setting | Default | Matlab |
|---|---|---|
| `MAX_DOWNLOAD_GB` | 20 | isse bada file skip |
| `MAX_UPLOAD_MB` | 2000 | public Bot API limit; bada file split hoga |
| `MAX_FOLDER_FILES` | 300 | ek folder scan me max files |
| `CONCURRENT_DOWNLOADS` | 2 (Railway: 1) | parallel transfers |
| `FOLDER_ORDER` | deep | sequence: module poora pehle |
| `KEEP_DOWNLOADED_FILES` | false | upload ke baad temp delete |

---

## Private + restricted files ka sach

- Bot **sirf wahi files padh sakta hai jo tumhare Google account ko dikhti hain**.
  Private/restricted files isi liye chalti hain — kyunki tumhara account unka
  owner/member hai, bypass nahi hota.
- Kisi aur ka locked content nikalne ka koi "trick" bot me nahi hai — wo
  credential abuse hai, aur chalta bhi nahi (Google tokens rotate karta hai).
- Auth sirf read-only hai (`drive.readonly`), password kahin nahi jaata, aur
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
  se kabhi bhi revoke kar sakte ho.

---

## Legal — ek paragraph, padh lena

Course tumne kharida hai ya tumhare account se share hua hai, to apne padhne ke
liye Telegram pe copy rakhna tumhara decision hai. Par us copy ko kisi public
channel pe **redistribute ya resell karna copyright infringement** hai, aur
Telegram bot + tumhara account dono ban ho sakte hain. Personal copy personal
rakho, aur course provider ke terms ek baar dekh lena.
