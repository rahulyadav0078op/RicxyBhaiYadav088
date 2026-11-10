# Deep audit — Oct 2026 (internet ke saath verify kiya)

Maine poora code re-check kiya, official docs padhe, aur 4 assumptions internet pe
verify kiye. **4 real bugs mile, teeno tumhare course use-case ko todte the.**
Sab fix ho gaye + regression tests add kiye. **59 tests pass.**

---

## Bug 1 — Public download URL galat host use kar raha tha (BADA)

**Kya tha:** anonymous mode me `https://drive.google.com/uc?export=download&id=…`

**Research (Oct 2026):** Google ne download host badal diya hai. `drive.google.com/uc`
ab **303 redirect** karta hai `drive.usercontent.google.com` par, **aur redirect me
`confirm` parameter drop kar deta hai**. Iska matlab ~100 MB se bade files par
virus-scan wala HTML page milta hai, file bytes nahi.

**Asar tumhare case me:** course ke saare videos 100 MB+ hote hain → public mode me
har bada video fail hota. (OAuth mode me Drive API use hota hai, isliye wahan
nahi dikhta — par rclone/public wale raaste me pakka todta.)

**Fix:**
```
https://drive.usercontent.google.com/download?id=<ID>&export=download&confirm=t
```
`confirm=t` wahi value hai jo "Download anyway" button submit karta hai — Google
ab isko fixed value ke taur pe accept karta hai.

Plus `_with_confirm()` ab legacy URL ko sahi host par rewrite karta hai, warna
retry loop me wahi HTML page aata rehta.

**Test:** `test_public_download_uses_usercontent_host`, `test_confirm_handshake_rewrites_legacy_host`

---

## Bug 2 — 20 second upload timeout (BADA, silent killer)

**Kya tha:** koi media timeout set nahi.

**Research:** python-telegram-bot v21 me `HTTPXRequest.media_write_timeout` ka
**default 20 seconds** hai — aur ye sirf file uploads par lagta hai (read/write
timeout se alag). Matlab: 2 GB video ka upload 20 s se lamba chala to connection
cut, `TimedOut`, aur tum wahi "upload failed" dekhte.

**Fix:** `builder.media_write_timeout(MEDIA_WRITE_TIMEOUT)` — default **1800 s**,
env var se override ho sakta hai.

**Test:** `test_media_write_timeout_is_raised_above_ptb_default`

---

## Bug 3 — 2 GB upload ka assumption galat tha

**Kya tha:** code + docs me `MAX_UPLOAD_MB=2000` "public API ka ceiling" likha tha.

**Research (contradiction mila, dono padhe):**
- Telegram ka **official Bot API docs aur Bots FAQ** aaj bhi kehte hain:
  *"Bots can currently send files of any type of up to **50 MB** in size"*
  (`core.telegram.org/bots/faq#how-do-i-upload-a-large-file`)
- Kuch third-party sources 2000 MB likhte hain; **local Bot API server** par
  2000 MB confirm hai (`core.telegram.org/bots/api` → Using a Local Bot API Server)

**Sach:** 50 MB se bade files public API par *kabhi-kabhi* chalti hain (Telegram
practically bada allow karta hai), par iska **koi guarantee nahi**. "2 GB chalega"
maan ke course bhejna = beech me fail hone ka risk.

**Fix:**
- `BOT_API_BASE_URL` set hone par ab **`local_mode(True)`** — PTB Telegram ko file
  ka *path* deta hai: 2000 MB tak, apni taraf se file download/stream nahi, kam RAM
- `.env.example` me honest note: public API = 50 MB documented; 2 GB chahiye to
  local Bot API server
- 2 GB se bada file pehle jaise split hota hai (wo behaviour wahi hai)

**Test:** `test_local_mode_passes_path_not_file_object`

---

## Bug 4 — Local mode me progress bar jhooth bolta

**Kya tha:** `TrackedReader` bytes count karta hai; par local mode me Telegram khud
file padhta hai, to counter 0% par atka dikhta.

**Fix:** local mode me static "Uploading <file>" state, aur streaming mode me live
percentage (jaise pehle).

---

## Ek assumption jo verify hua ✅ (no bug)

| Assumption | Verification |
|---|---|
| Refresh token Testing mode me 7 din me expire | ✅ Confirmed (Google docs + multiple 2026 sources). **Isliye "Publish app" step zaroori hai** — warna har hafte `/auth` tootega |
| Published app + 6 months inactivity = token expire | ✅ Confirmed — bot 6 mahine chalu na ho to token marr jaata hai |
| 100 refresh tokens per client per user cap | ✅ Confirmed — purane token silently revoke hote hain |
| `send_document(filename=...)` PTB 21 me valid | ✅ Confirmed (inspected 21.9 signature) |
| `csv`/`xlsx` export, `supportsAllDrives`, `nextPageToken` | ✅ Code me sahi use ho raha hai |

---

## Jo cheezein main *nahi* badal sakta (tumhare side)

| Cheez | Kyun |
|---|---|
| Google ka "Allow" click | Tumhare account ki consent — meri pahunch nahi |
| Render/GitHub me env vars | Dashboard tumhara |
| `Publish app` (7-day token fix) | Cloud Console tumhara |
| Paid course ka DRM/lock todna | Ye main nahi karunga — access control bypass |
| Bot me email/password | Google ToS ke against, 2FA se toot jaata hai, account lock risk |

---

## Deployment impact — ab kya karna hai

| Step | Kya |
|---|---|
| 1 | Naya ZIP download → GitHub me replace → Render auto-deploy |
| 2 | Render → Environment me `MEDIA_WRITE_TIMEOUT=1800` add karo (optional, default hi hai) |
| 3 | Telegram → `/doctor` → sab ✅ |
| 4 | Folder link → **▶️ Send all** |
| 5 | **Video 200 MB+ upload fail ho to:** public API 50 MB limit ka maamla hai — local Bot API server setup karna padega (`BOT_API_BASE_URL`), warna bot file ko auto-split karega |

---

## Numbers (research se)

| Cheez | Value | Source |
|---|---|---|
| Bot upload (public API, documented) | 50 MB | core.telegram.org/bots/faq |
| Bot upload (local server) | 2000 MB | core.telegram.org/bots/api |
| Bot download via getFile | 20 MB | core.telegram.org/bots/api |
| PTB media write timeout default | **20 s** | docs.python-telegram-bot.org |
| Drive large-file threshold | ~100 MB | multiple 2026 sources |
| Google refresh token (Testing) | 7 days | Google / Stack Overflow |
| Google refresh token (Production, active) | indefinite (6-month inactivity rule) | Google |

---

## Test suite

```
59 passed
```

Isme 6 naye tests usi research se:
- usercontent host + confirm=t
- legacy URL rewrite
- OAuth mode still uses Drive API
- media_write_timeout raised
- local_mode → path upload
- (plus earlier) credential error messages

Ek line me: **code ab official docs ke hisaab se sahi hai**, aur jo ek cheez
pakki nahi thi (2 GB) usko docs me honest kar diya.
