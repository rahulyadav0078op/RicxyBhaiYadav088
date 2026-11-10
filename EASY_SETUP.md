# Sabse aasan setup — Google Cloud Console ke bina

Tumhara sawaal: "client banane ka jhanjhat automate ho sakta hai?" — **haan.**

`setup_wizard.py` ek hi command me poora kaam karta hai: authorize → token →
validate → `.env` likhna → host ke liye paste-ready block.

```bash
python setup_wizard.py
```

```
1) rclone        — Cloud Console / Playground / redirect URI  ❌ kuch nahi
2) apna client   — Cloud Console se (apna quota, 5 min)
3) public only   — koi setup nahi
4) sirf check    — .env validate karo
```

---

## Raasta 1 — rclone (recommended, console zero)

**Kyun chalta hai:** rclone ke paas apna registered OAuth client hai, to Google
ko normal consent screen dikhta hai. Tumhe koi project, client, scope, redirect
URI banane ki zaroorat nahi.

### Kya chahiye
- `rclone` install (ek command)
- Ek baar browser me "Allow" (phone ya laptop, dono chalega)

### Steps

```bash
# 1) rclone install
sudo apt install rclone        # Ubuntu/Debian
# macOS: brew install rclone
# Termux (Android): pkg install rclone

# 2) wizard chalao
python setup_wizard.py --path rclone
```

Wizard khud:
1. `rclone config create gdrive drive scope drive.readonly` chalata hai
2. Browser kholta hai → tum apna Gmail chunte ho → **Allow**
3. Token import karta hai (`import_rclone_token.py`)
4. **Google se test karta hai** — token valid hai ya nahi, scope me Drive hai ya nahi
5. Course folder ID poochta hai (optional) aur us folder ko list karke dikhata hai
6. `.env` me 3 lines likh deta hai (chmod 600) + Render/Railway ke liye block print karta hai

### Browser-less server pe?
`rclone config create ...` URL print karega — usko phone ke browser me kholo,
Allow karo, jo code mile wo terminal me paste karo. (`rclone authorize "drive"`
bhi yahi karta hai.)

### Ek honest catch
rclone ka client id **sab rclone users ke saath shared** hai. Google kabhi-kabhi
uspe rate limit lagata hai. Bade course (100+ files) me kabhi 403 aaye to Raasta 2
(apna client) kar lo — free hai, 5 minute.

---

## Raasta 2 — apna client (Cloud Console, 5 min)

Agar tum apna quota chahte ho:

```bash
python setup_wizard.py --path own
```

Wizard steps dikhata hai, phir Client ID / Secret / Refresh token leta hai (laptop
pe `python oauth_setup.py` se), validate karta hai, aur `.env` likh deta hai.

**Yehi tumhare `redirect_uri_mismatch` ka pakka fix hai** — `oauth_setup.py`
localhost loopback use karta hai, isliye Playground wali URI register karne ki
zaroorat hi nahi padti.

---

## Raasta 3 — public only

Course ke files "anyone with the link" hain? Kuch configure mat karo.
Bot single file links bhej dega. (Folder scan nahi hoga — Google anonymous
clients ko listing nahi deta.)

---

## Validate / re-check kabhi bhi

```bash
python setup_wizard.py --check-only
```

Output: token valid?, scope me `drive.readonly`?, aur host ke liye ready env block.
Galat ho to exact wajah (`invalid_client` / `invalid_grant` / no Drive scope).

---

## Host pe paste karne ke baad

```env
# Render → Environment (worker service, disk mounted at /data)
BOT_TOKEN=...
ALLOWED_USER_IDS=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
DATA_DIR=/data
TEMP_DIR=/data/tmp
JOBS_DB=/data/jobs.sqlite3
FOLDER_ORDER=deep
MAX_DOWNLOAD_GB=15
CONCURRENT_DOWNLOADS=1
```

→ Redeploy → Telegram pe `/auth` → Gmail dikhe ✅ → folder link → **▶️ Send all**

---

## Jo tumne poocha tha — "login into the drive, connecting something"

Do cheezein alag hain:

| Chaha | Possible? |
|---|---|
| Bot tumhare Google account se connect ho (proper tarika) | ✅ **Yahi ho raha hai** — OAuth read-only consent, rclone ya apna client |
| Bot me email/password daal ke login | ❌ Nahi. Bot me password daalna Google ToS ke against hai, 2FA/captcha se toot jaata hai, aur ek din me account lock ho jaayega. Isliye nahi banaya |
| Kisi aur ka locked course kholna | ❌ Nahi. Access-control bypass — main wo nahi banaunga |

OAuth hi woh "connecting something" hai jo tum chahte ho — bas rclone wale raaste
me wo 2 click ka kaam hai, koi console nahi.

---

## Render logs me jo dikha

```
Google credentials loaded (authorized mode). ✅
telegram.error.NetworkError: http.ConnectError  ⚠️
→ Instance restarted
```

- `NetworkError` = Telegram se transient connection drop. Bot khud retry karta hai;
  Render auto-restart karta hai. Normal hai, panic mat karo.
- Credentials load ho gaye — matlab env vars mil gaye.
- **Confirm karne ke liye Telegram pe `/auth` bhejo.** Gmail dikha = ready.
  `invalid_client` aaya = token/client mismatch → `python setup_wizard.py --check-only`
