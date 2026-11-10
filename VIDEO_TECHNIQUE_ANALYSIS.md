# Video ka deep analysis — har step, aur kya automate ho sakta hai

**Video:** "How to Download Restricted Google Drive Video (1080p/2K/4K) – View Only
File Download Trick 2025" — MaxiPy, 6:24, ~53k views.

Maine transcript poora padha. Neeche har step, uska technical matlab, aur bot me
kya automate ho sakta hai.

---

## Step-by-step breakdown (jaisa video me hai)

| # | Video ka step | Technically kya ho raha hai | Bot me automate? |
|---|---|---|---|
| 1 | F12 / Ctrl+Shift+I → **Network** tab | DevTools network capture on | ✅ HTTP calls se seedha |
| 2 | Play video → gear → **highest quality** | Player ko max rendition select karwa rahe hain, taki network me 1080p/2K/4K track load ho | ⚠️ partial (neeche) |
| 3 | **Size** column pe 2 click → sabse badi file | Video track (largest range request) | ✅ |
| 4 | Right click → **Copy URL** | Wo `videoplayback?...&mime=video%2Fmp4` URL | ✅ |
| 5 | Filter box me **"audio"** type | Audio-only track alag hai (DASH-style split) | ✅ |
| 6 | Audio URL copy | `mime=audio%2Fmp4` URL | ✅ |
| 7 | Dono links se `&range=...` aur tail **delete** karna | Range param hataana — warna sirf ek chunk milta hai | ✅ |
| 8 | Link browser me kholna → download | Direct GET (ab poore bytes) | ✅ |
| 9 | "Video me audio nahi hai" — audio alag download | Confirm: **2 separate tracks** | ✅ |
| 10 | **Video editor me merge** karna | 🔴 **Yahan quality loss + slow** | ✅ **ffmpeg `-c copy`** |
| 11 | "Preferred resolution me edit karo" | Re-encode = aur quality loss | ✅ avoid |

**Video ka core insight (jo sahi hai):** Drive "view-only" hone par **file download
block** hota hai, par **streaming block nahi hoti** — player ko bytes chahiye hi
hote hain. Aur Google **DASH-style** serve karta hai: video track alag, audio track
alag. Isliye download me audio missing aata hai.

---

## Video ke 3 asli weaknesses (jo maine fix kiye)

### Weakness 1 — "Video editor me merge karo" 🔴
Video editor me merge = **re-encode** = (a) slow, (b) quality loss, (c) GUI chahiye,
(d) 4K video pe laptop ghut jaata hai.

**Sahi tarika:** FFmpeg **stream copy** — decode/encode bilkul nahi, bytes wahi
rehte hain jo Google ne bheje:

```bash
ffmpeg -i video.mp4 -i audio.m4a -map 0:v:0 -map 1:a:0 -c copy \
       -shortest -movflags +faststart output.mp4
```

**Ye maine bana diya — `media_mux.py` + `/merge` command.** Handle karta hai:
- `-c copy` (zero quality loss)
- `-shortest` (audio/video length mismatch)
- `-itsoffset` (A/V drift jo DASH me common hai)
- mp4 jo audio codec accept nahi kare → **audio ko AAC** karo, video copy hi rahe
- phir bhi fail → **.mkv** fallback (kuch bhi hold karta hai)
- **post-merge verify** — "audio gayab" wala silent failure pakadta hai

### Weakness 2 — Manual DevTools scraping 🔴
Har file ke liye F12, filter, copy, paste, `range` delete... 300 files = 300 baar.
**Automate:** page se URLs regex se nikaalna + `Range` header se poora file lena.
Bot me `get_stream_parts()` ye karta hai.

### Weakness 3 — "Highest quality" step ka assumption 🔴
Video kehta hai "gear se highest quality select karo" — par us se sirf **player**
max rendition load karta hai. Agar viewer page me DASH manifest hai to **saare
renditions** pehle se available hote hain; agar progressive single file hai to ek
hi track hota hai. Yaani step 2 mostly **bekaar** hai — network list me sabse badi
video entry pehle se hoti hai.

---

## Ye technique kab kaam karti hai, kab nahi (honest limits)

| Situation | Kaam karega? | Kyun |
|---|---|---|
| Public / "anyone with link" view-only video | ✅ | anonymous viewer ko stream milta hai |
| Download-disabled but link-shared | ✅ | streaming ≠ download permission |
| **Private** (account-only) video | ❌ | viewer page sign-in maangta hai; tumhara **OAuth token** chahiye |
| DRM-protected (Widevine) | ❌ | segments encrypted; key ke bina kuch nahi |
| Google Docs/Slides ka video embed | ⚠️ | alag endpoint |

**Tumhare course file ke liye sabse reliable raasta phir bhi OAuth + Drive API hai**
(jo bot me pehle se hai) — kyunki wahan ACL tumhare account se satisfy hota hai.
Stream-scraping sirf **public view-only** case ka backup hai.

---

## Bot ka final pipeline (ab)

```
Drive folder link
   │
   ├─ OAuth + Drive API  ──────────────► alt=media (original bytes, private OK)
   │      └─ 403 quota? ──► /quotafix ─► copy banao → copy download → copy delete
   │
   ├─ Public view-only video
   │      └─ stream parts ──► video track + audio track
   │                             └─ media_mux (ffmpeg -c copy) ──► merged MP4
   │
   └─ Upload: send_video (streaming, no transcode) / auto-split >2 GB
```

---

## Jo code me add hua (final)

| Cheez | File | Tests |
|---|---|---|
| `get_stream_parts()` — viewer page se video/audio URLs, **mp4 prefer + highest itag** | `gdrive_core.py` | — |
| `media_download.py` — ek track ka resumable download, `&range=` strip (step 7), stale-link / sign-in / HTML-page detection | **new** | **10** (`test_media_download.py`, real local HTTP server) |
| `media_mux.py` — lossless merge, 5 failure modes handled, ffprobe → ffmpeg fallback | new | **10** stub + **5** real-binary (`test_ffmpeg_real.py`) |
| `/streamdl` — poora video pipeline ek command me (2 tracks → mux → verify → send, upload cap ke hisaab se split) | `bot.py` | **4** |
| `/merge` — chat me do tracks bhejo, merged file milegi | `bot.py` | — |
| `AUTO_QUOTA_FIX` — quota wall khud handle (copy → download → delete) | `bot.py` | **3** |
| Dockerfile me **ffmpeg + ffprobe** + build-time assert | `Dockerfile` | — |
| `selftest.py` — host par poora functional check (mux pipeline included) | **new** | — |
| `env_check.py` — deploy se pehle galat token/user-id/disk pakadta hai | **new** | — |
| `oauth_helper.py` — refresh token, local server **ya** `--manual` (phone se bhi) | **new** | — |

**Total: 96 tests pass** (ffmpeg wale real-binary tests auto-skip ho jaate hain
jahan binary nahi hai).

### `/streamdl` ka flow

```
/streamdl <link>
   └─ get_stream_parts()        → video-only + audio-only URLs (mp4, highest itag)
       └─ clean_stream_url()    → `&range=...` hatao (tutorial ka step 7)
           └─ fetch_track() x2  → resumable, .part, retry, stale-link detect
               └─ mux(-c copy)  → video copy + audio copy, -shortest, +faststart
                   └─ verify()  → "audio stream hai?" — silent mute failure pakadta hai
                       └─ upload → >limit ho to parts me split
```

---

## Tumhare "login" wale sawaal ka seedha jawaab

Video me login **nahi** chahiye — bas public viewer page chahiye. Aur main password
se login **nahi** banaunga, kyunki:

1. Google ToS violation, 2FA/captcha se toot jaata hai
2. Session-cookie theft se **poora Google account** (Gmail, Drive, payments) risk me
3. Tumhare paas already legit raasta hai: **OAuth read-only** — us se tumhare
   account ko jo dikhta hai, sab milta hai, including private course
   (`GOOGLE_OAUTH_PHONE_GUIDE.md`)

---

## Ab kya — sab ready hai

| Chaho | Kahan |
|---|---|
| Deploy (Railway) | `RAILWAY_STEP_BY_STEP.md` |
| Deploy (Render) | `RENDER_STEP_BY_STEP.md` |
| Google access | `GOOGLE_OAUTH_PHONE_GUIDE.md` |
| Local Bot API server (2 GB single upload) | abhi build nahi hua — bolo to bana dun |
