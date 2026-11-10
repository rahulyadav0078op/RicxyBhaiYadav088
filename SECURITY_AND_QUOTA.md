# "Kuch bhi download karo, bina issue" — sach, research ke saath

Tumne poocha: email/password se login, cookies, "mirror" bots ki tarah sab kuch.
Main poori research kar chuka hoon. Yahan woh sab hai — **kya possible hai, kya nahi,
aur kyun.** Koi lecture nahi, sirf facts + jo main actually bana sakta hoon.

---

## 1. Tumhare screenshots wala bot — kaise kaam karta hai (aur khatra)

Screenshots me 2 alag cheezein dikh rahi hain:

| Option | Kya karta hai | Sach |
|---|---|---|
| **Google OAuth Login** | Google ka official consent | ✅ **Yehi sahi hai.** Ye main bhi support karta hoon |
| **Auto Login (Gmail/Password)** | Tumhara password bot ke server pe jaata hai | 🔴 **Kabhi mat karo** (neeche detail) |
| **Paste Cookies (.json)** | Browser session cookies | 🔴 **Ye password se bhi zyada khatarnak hai** |

### Cookies kyun password se zyada khatarnak hain

Session cookies = **already logged-in session ka chaabi**. Password nahi, par:

- 2FA **already bypass** ho chuka hota hai (session 2FA ke baad bana tha)
- Research (Forbes / CloudSEK / GBHackers): cookie-stealer malware **password change
  ke baad bhi** Google account access bana ke rakhta hai — "session persistence"
- Matlab: cookie de di → wo banda tumhara **Gmail, Drive, Photos, YouTube, payments,
  sab** access kar sakta hai. Aur password badalne se bhi nahi rukta
- Ek aur baat: **Telegram pe cookie bhejna = woh file tumhare Telegram cloud me
  hamesha ke liye hai**, jahan se bot owner download kar sakta hai

### "Auto Login (Gmail/Password)" kyun kaam hi nahi karega

| Problem | Detail |
|---|---|
| Google ToS | Password se automated login explicitly prohibited |
| 2FA / OTP | Tumhara account 2FA pe hai to bot ruk jaayega |
| CAPTCHA | Google automated login pe captcha dega |
| Device verification | Naye server IP se login = "suspicious" → account lock |
| **Result** | Jis din tumhara account lock hoga, uska koi recovery nahi hoga bot ke paas |

Jo bots ye offer karte hain wo ya to **fraud** hain, ya unka account kuch din me
ban ho jaata hai. Isliye main ye nahi banaunga — tumhara nuksaan hoga.

---

## 2. Asli technical problem: "download quota exceeded"

Reddit thread (`r/DataHoarder`) ki asli problem yehi hai. Ye **permission** ka issue
nahi hai — file tumhare account ko dikhti hai. Ye Google ka **throttle** hai:

```
403 downloadQuotaExceeded
"Too many users have viewed or downloaded this file recently"
```

**Kaise kaam karta hai (research):** Google ye limit **file object** par lagata hai,
account par nahi. Wahi file jab bahut logon ne download ki, us file ka egress
throttle ho jaata hai — rolling window, kuch ghante se 24 ghante.

### Jo kaam NAHI karta (log ye try karte hain, time waste hota hai)
- ❌ VPN / IP badalna — limit server-side hai, file object par
- ❌ Cookies clear karna, incognito, doosra browser
- ❌ URL me `uc` ko `open` kar dena
- ❌ "Bypass" websites (gdbypass etc.) — mostly dead, ya wahi quota wall

### Jo actually kaam karta hai ✅

| Method | Kaise | Bot me? |
|---|---|---|
| **Make a copy in your own Drive** | Copy = **naya file object** → uski quota zero → turant download | ✅ **`/quotafix` bana diya** |
| Wait 1–24 h | Rolling window reset | ✅ bot retry kar sakta hai |
| Owner se dobara upload / Shared Drive me move | Naya object | ❌ owner ka kaam |
| Authorized API download | Drive API `alt=media` (OAuth) | ✅ pehle se hai |

**`/quotafix` kaise chalta hai:**
```
copy (naya object) → copy download karo (fresh quota) → Telegram pe bhejo → copy delete
```
Tumhari Drive me sirf kuch second ke liye ek temp copy banti hai, phir delete.
Agar tumhari storage full hai to bot saaf error dega.

**Ek trade-off, honestly:** copy banane ke liye **full `drive` scope** chahiye
(readonly se file create nahi hoti). Aur `drive` scope ka matlab hai bot technically
tumhari Drive me file create/delete kar sakta hai. Isliye ye **default off** hai:
`DRIVE_SCOPE=full` set karo tabhi chalta hai. Bot sirf apni banayi copy delete karta
hai — tumhari baaki files ko haath nahi lagata (code me sirf wahi id delete hoti hai
jo usne khud banayi).

---

## 3. "Kisi aur ka locked course khol do"

| Chaho | Possible? |
|---|---|
| File tumhare account ko **dikhti** hai (shared/member/owner) | ✅ **Bilkul** — bot wahi karta hai, quota wall bhi handle karta hai |
| File tumhare account ko **nahi dikhti** (kisi aur ka private) | ❌ Nahi — aur koi bhi "trick" kaam nahi karta. ACL server-side hai |

Jo "mirror" bots sab kuch download karte dikhte hain, unke paas **public links** ya
**shared files** hote hain, ya wo tumhare hi account se OAuth lete hain. Koi jaadu
nahi hai.

Main access-control bypass (kisi aur ka private content) nahi banaunga — ye credential
abuse hai aur legally bhi galat. **Par tumhare apne accessible content me koi
restriction nahi** — folder, subfolder, 300 files, videos, PDFs, sab.

---

## 4. Telegram ka 2 GB limit — jo tumhare screenshots wale bot me "solve" dikhta hai

Wo bot **local Bot API server** chalata hai (isliye `Dump Channel`, `Boost Mode` dikh
raha hai — MTProto + local server). Isse:
- upload 2000 MB tak
- file `file_id` se dobara bhejna (re-upload nahi)

Mera bot me `BOT_API_BASE_URL` set karke wahi hota hai (`local_mode`), aur uske bina
file **auto-split** ho jaati hai. Local Bot API server setup karna hai to bolo —
guide bana dunga (Docker me 1 service).

> Note: ye "Dump channel" wala design tabs ko **pirated content redistribute** karne
> ke liye use hota hai. Main wo feature nahi banaunga — personal backup tak theek hai,
> public channel pe course distribute karna copyright + ban risk hai.

---

## 5. Jo bot me ab hai (tumhare use case ke liye best)

| Feature | Status |
|---|---|
| Folder → poora course, sequence me | ✅ |
| Private/restricted files (OAuth read-only) | ✅ |
| **Quota exceeded → `/quotafix`** | ✅ **naya** |
| Resume + dedupe (restart safe) | ✅ |
| 2 GB+ (local Bot API server) | ✅ optional |
| `/doctor` self-diagnosis | ✅ |
| Cookies / password login | ❌ **jaan-boojh ke nahi** (neeche kyun) |

---

## 6. Tumhare liye safest setup (2 minute)

1. `python setup_wizard.py --path rclone` → read-only token (Google Cloud Console nahi chahiye)
2. Render pe deploy (ya laptop pe `python bot.py`)
3. Folder link bhejo → **▶️ Send all**
4. Koi file "quota exceeded" bole → **`/quotafix <link>`**
   (iske liye `DRIVE_SCOPE=full` + dobara authorize karna padega — bot khud batayega)

---

## 7. Ek line me

- **Tumhare accessible content** = 100% download ho jaayega, quota wall bhi handle
- **Password/cookies** = kabhi nahi (tumhara poora Google account risk me)
- **Kisi aur ka private content** = nahi (aur koi trick kaam nahi karta)
- Jo "sab kuch bypass" waale bots dikhte hain — unme se zyada tar **scam** hain, ya
  account ban risk, ya (sabse common) wo bas OAuth se tumhara hi access use karte hain

Research sources: Forbes (session-cookie hijack), CloudSEK/GBHackers (session
persistence after password reset), core.telegram.org/bots/faq (upload limits),
Google Drive API docs (copy endpoint), r/DataHoarder thread (quota reality).
