# Phone checklist — Google creds + Railway

Tumhara current status:

- ✅ Project `gdrive-telegram` bana
- ✅ Google Drive API **Enabled**
- ✅ OAuth client bana ("OAuth configuration created" toast aaya tha)

Baaki 3 values chahiye bot ko: **Client ID**, **Client secret**, **refresh_token**.
Uske baad Railway.

---

## Step A — OAuth consent screen check + Publish

Left menu → **OAuth consent screen**

Dekho kya dikh raha hai:

| Screen pe dikhe | Kya karo |
|---|---|
| **"Publish app"** button | ✅ **Publish app dabao** → Confirm. (Bahut zaroori: warna refresh token **7 din** me expire ho jaayega.) |
| App already **"In production"** | ✅ already done, aage badho |
| **"Start configuration"** / "not configured" | Configure karo: App name `gdrive-bot` → support email apna → Audience **External** → Save. Phir **Data access → Add scopes** → `.../auth/drive.readonly` → Save. Phir **Publish app** |
| Audience **Internal** dikhe | Ye sirf Workspace account me hota hai — theek hai |

**Test users** section me apna Gmail hona chahiye (na ho to Add users → apna email).

---

## Step B — Client ID + Secret copy karo

Left menu → **Credentials**

1. **OAuth 2.0 Client IDs** section me apna client dikhega (naam `gdrive-bot` jaisa, Type **Desktop**)
2. Uspe tap karo
3. **Client ID** → copy karo (lamba, `....apps.googleusercontent.com` pe khatam)
4. **Client secret** → copy karo (`GOCSPX-...` se shuru)

Notepad me rakh lo — 2 me se 1 value mil gayi:

```
GOOGLE_CLIENT_ID=      ← copy
GOOGLE_CLIENT_SECRET=  ← copy
GOOGLE_REFRESH_TOKEN=  ← Step C me milega
```

> Client dikh raha hai par Type "Web application" hai? Chalega — Playground me
> bhi kaam karega (bas redirect URI wala step skip karna).

---

## Step C — refresh_token (phone se, OAuth Playground)

> ### ⚠️ 3 traps jo screenshot me saaf dikhte hain
>
> **1. Playground ka apna client use mat karo.** Agar request me
> `client_id=407408718192.apps.googleusercontent.com` dikh raha hai, to wo
> Playground ka built-in client hai — tumhare project ka nahi. Us token ko bot
> use nahi kar sakta (refresh token us client se bandha hota hai), aur secret
> masked (`************`) dikhega. **Gear me "Use your own OAuth credentials"
> tick karke apna Client ID + Secret dalna zaroori hai.** Tumhare client ID ka
> number Credentials page wale se match karna chahiye.
>
> **2. `refresh_token_expires_in: 604799` (= 7 din) dikhna = app Testing mode
> me hai.** Publish karo (Step A) warna ek hafte me `/auth` toot jaayega.
>
> **3. Token kabhi screenshot/share mat karo.** Refresh token = tumhari Drive ka
> long-lived access. Leak ho jaye to
> https://myaccount.google.com/permissions se **Remove access** karke naya banao.

`oauth_setup.py` laptop maangta hai, to phone pe Playground:

1. Kholo: **https://developers.google.com/oauthplayground**
2. Top-right **⚙️ gear icon** → **"Use your own OAuth credentials"** pe tick
3. **OAuth Client ID** → Step B ka Client ID paste
   **OAuth Client secret** → Step B ka secret paste
4. **Close**
5. Left panel me scroll → **Drive API v3** → `https://www.googleapis.com/auth/drive.readonly`
   pe **tick** (sirf yahi, `drive` full nahi)
6. Neeche **Authorize APIs** dabao
7. Apna Gmail chuno → "Google hasn't verified this app" → **Advanced** →
   **Go to gdrive-bot (unsafe)** → **Allow** (read-only hai)
8. **Step 2: Exchange authorization code for tokens** dabao
9. Right side **Request / Response** me JSON aayega — usme **`"refresh_token": "..."`**
   copy karo

```
GOOGLE_REFRESH_TOKEN=1//0g...   ← ye mil gaya
```

**Verify karo ki sahi client use hua:** response me `scope` =
`https://www.googleapis.com/auth/drive.readonly` hona chahiye, aur
`refresh_token_expires_in` **nahi** dikhna chahiye. Agar wo field dikhe, app
abhi Testing mode me hai → Step A (Publish) karke dubara authorize karo.

> `refresh_token` na dikhe? Gear me tick karke **Authorize APIs** dobara karo —
> Google sirf pehli baar (consent par) refresh token deta hai. Ya account se
> https://myaccount.google.com/permissions pe app ka access hatake dubara karo.

---

## Step D — Telegram values

| Value | Kahan se |
|---|---|
| `BOT_TOKEN` | Telegram pe [@BotFather](https://t.me/BotFather) → `/newbot` → naam + username → token `123456:AA...` |
| `ALLOWED_USER_IDS` | Telegram pe [@userinfobot](https://t.me/userinfobot) → tumhara numeric id |

---

## Step E — Railway

1. GitHub pe code (Railway → Deploy from GitHub ke liye chahiye)
2. https://railway.app → GitHub login → **New Project → Deploy from GitHub repo**
3. Service → **+ → Volumes** → mount path **`/data`** → Add
4. **Variables → Raw Editor** → paste:

```env
BOT_TOKEN=123456789:AA...
ALLOWED_USER_IDS=123456789
GOOGLE_CLIENT_ID=....apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
GOOGLE_REFRESH_TOKEN=1//0g...
DATA_DIR=/data
TEMP_DIR=/data/tmp
JOBS_DB=/data/jobs.sqlite3
FOLDER_ORDER=deep
MAX_DOWNLOAD_GB=15
MAX_UPLOAD_MB=2000
CONCURRENT_DOWNLOADS=1
LOG_LEVEL=INFO
```

5. **Deploy** → logs me `Bot up as @your_bot` dikhe

---

## Step F — Verify + course

Telegram pe:

1. `/start` → Mode: **authorized**
2. `/auth` → tumhara Gmail dikhna chahiye ✅
3. Course folder link bhejo: `https://drive.google.com/drive/folders/<ID>`
4. Numbered preview aayega → **▶️ Send all to Telegram**
5. `/jobs` = progress · `/cancel` = stop · `/clear` = ledger reset

---

## `invalid_client: The OAuth client was not found` — exact fix

Ye error 4 me se 1 wajah se aata hai. Diagnostic chalao (laptop pe ya Railway shell me):

```bash
python diagnose_credentials.py           # .env padhta hai
```

Wo masked values, format problems, aur Google ka asli jawaab dikha dega.

| Wajah | Pehchan | Fix |
|---|---|---|
| **Refresh token dusre client ka hai** | Playground me request me `client_id=407408718192...` dikha tha | Playground **⚙️ gear → "Use your own OAuth credentials"** tick → apna ID+secret → **Authorize APIs** dubara |
| **Client ID galat/delete ho gaya** | Credentials page pe client hi nahi, ya ID galat copy hua | Credentials → OAuth 2.0 Client IDs → naya **Desktop app** client → naya token |
| **Quotes/spaces value me chale gaye** | Railway me `"123-abc..."` quotes ke saath paste hua | Variable edit karke quotes hatao (value sirf number+dash se shuru ho) |
| **Client ID me kuch aur paste ho gaya** | Client ka *naam* ya project number | Credentials page se **Client ID** column copy karo (`...apps.googleusercontent.com`) |

**Sabse reliable raasta (2 minute):**

1. Credentials → apna **Desktop** client → Client ID + Client secret copy (fresh)
2. Playground → **⚙️ gear** → *Use your own OAuth credentials* ✅ → dono paste → Close
3. **Authorize APIs** → Allow → **Step 2: Exchange authorization code for tokens**
4. Naya `refresh_token` copy (`1//...` se shuru)
5. Railway → Variables → teeno values replace → Redeploy
6. Telegram pe `/auth` → ✅

**Zaroori:** teeno values **ek hi client** ki honi chahiye. Token jis client se bana,
wahi ID+secret use karo — warna Google `invalid_client`/`invalid_grant` dega.

## `Error 400: redirect_uri_mismatch` — exact fix

Sign-in page pe "Access blocked: This app's request is invalid" + `redirect_uri_mismatch`.

Matlab: client ID sahi hai (achhi baat ✅), par Playground ka return address us
client me registered nahi hai.

**Credentials → apne client pe tap → Type dekho:**

| Type | Fix |
|---|---|
| **Web application** | **Authorized redirect URIs → + Add URI** → paste exactly `https://developers.google.com/oauthplayground` → **Save** → **5–10 min wait** (propagation) → Playground retry |
| **Desktop app** | Playground is type ke saath kaam nahi karta. Ya to naya **Web application** client banao (upar wali URI ke saath), ya laptop pe `python oauth_setup.py` chalao (loopback — koi URI nahi chahiye) |

Common galtiyan:
- URI ke aage/peeche space ya trailing `/` → mismatch
- `http://` likh diya (`https://` hona chahiye)
- Save karke turant retry — 5 min rukna zaroori hai
- Client badal liya par token purane client ka — teeno values ek hi client ki honi chahiye

## Agar atke

| Problem | Fix |
|---|---|
| Playground me "invalid_client" | Client ID/secret galat ya extra space — dubara copy |
| `redirect_uri_mismatch` | Gear me apne credentials use nahi kiye — Step C2 dobara |
| `403 access_denied` | Scope galat tick hua, ya app Testing mode me test user add nahi |
| `/auth` me error "invalid_grant" | Refresh token expire (app Publish nahi hai) — Step A + Step C dobara |
| Bot replies nahi | `ALLOWED_USER_IDS` me tumhara id, aur token sahi — logs dekho |
