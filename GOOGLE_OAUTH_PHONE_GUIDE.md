# STEP 0 — Google se refresh token nikalna (phone se, 15 minute)

Yeh ek hi kaam hai: bot ko **read-only** access dena taaki tumhare Google
account ke private course files bhi download ho sakein.

> **Tumhara Google password bot ko kabhi nahi dena hai.** Tum google.com par
> khud sign in karte ho, Google tumhe ek **code** deta hai, code se refresh
> token banta hai. Token sirf Drive padh sakta hai.

---

## A. Google Cloud project + Drive API (5 min)

1. Phone browser me kholo: **https://console.cloud.google.com**
2. Top bar me project dropdown → **New Project** → naam `gdrive-bot` → **Create**
3. Left menu (☰) → **APIs & Services** → **Library**
4. Search: `Google Drive API` → open → **Enable** (30 sec lagta hai)

## Scopes kya hai (simple shabd me)

**Scope = ek permission ka naam.** Jaise phone app "Camera" ya "Contacts" ki
permission maangta hai, waise hi OAuth app Google se permissions maangta hai.
Har permission ka ek **lamba naam** hota hai jo `https://www.googleapis.com/auth/…`
se shuru hota hai — wahi "scope" hai. Bas.

Bot ko sirf **ek** chahiye:

| Scope (exact string) | Matlab | Bot use kyun karta hai |
|---|---|---|
| `https://www.googleapis.com/auth/drive.readonly` | Drive ki files **padh sakta hai, badal nahi sakta** — dekhna, download karna, folder list karna | course files download karne ke liye |

Isme **delete, edit, share, upload** — kuch nahi aata. Bot tumhari koi file
mita nahi sakta, nayi nahi bana sakta. Sirf padhta hai.

### Baaki scopes kyun nahi

| Scope | Kya deta hai | Kyun nahi |
|---|---|---|
| `drive.file` | sirf woh files jo app ne khud banayi | ❌ tumhari purani course files nahi dikhengi |
| `drive.metadata.readonly` | sirf naam/size, content nahi | ❌ download nahi hoga |
| `drive` (full) | padhna + likhna + delete | ⚠️ sirf tab chuno jab `/quotafix` (copy-trick) chahiye — `DRIVE_SCOPE=full` |
| `drive.readonly` ✅ | sab kuch padhna | ✔️ **yahi chuno** |

### Screen par kya karna hai (3 tareeke, jo dikhe wahi)

1. **Filter/search box** (sabse aasan): box me `drive` type karo → list chhoti ho
   jayegi → `.../auth/drive.readonly` ke saamne **checkbox** tick → **Update**
2. **"Add or remove scopes"** panel: list scroll karke
   `https://www.googleapis.com/auth/drive.readonly` dhoondo → tick → **Update**
3. **"Manually add scopes"** naam ka box dikhe to usme poori string paste karo:
   ```
   https://www.googleapis.com/auth/drive.readonly
   ```
   → **Add to table** → **Update**

Kuch bhi samajh na aaye to **koi scope tick na karo** aur aage badh jao — token
phir bhi banega, par Google sirf basic profile dega aur Drive files nahi
dikhengi. Aise case me baad me Playground (Step D1, point 5) se scope dobara
add kar sakte ho.

> ⚠️ **`drive.readonly` "sensitive/restricted" scope hai.** Isliye Google
> "unverified app" ki warning dikhata hai — yeh normal hai, *Advanced → Go to
> gdrive-bot (unsafe)* karke aage badhna hai. Personal use ke liye verification
> ki zaroorat nahi.

### Sabse aasan raasta (scope screen hi skip karo)

Agar consent screen par scope dhoondhna hi headache lag raha hai:
**Step B8 me kuch bhi tick na karo**, aage badho, aur scope sirf
**OAuth Playground (Step D1, point 5)** me add karo — wahan list me
`Drive API v3` expand karke `.../auth/drive.readonly` tick karna hota hai.
Token wahi banta hai. Bahut log yahi karte hain.

---

## B. OAuth consent screen (3 min)

5. ☰ → **APIs & Services** → **OAuth consent screen** (naye console me
   "Google Auth Platform" → *Audience* / *Branding*)
6. User type: **External** → **Create**
7. App name: `gdrive-bot`, support email: tumhara email → **Save and continue**
8. **Scopes** — yahi woh confusing screen hai, detail neeche
   [§ Scopes kya hai](#scopes-kya-hai-simple-shabd-me) me hai. Short version:
   **Add or remove scopes** → filter box me `drive` type karo →
   `https://www.googleapis.com/auth/drive.readonly` ke saamne **checkbox tick** →
   **Update** → **Save and continue**
   (agar `drive.metadata.readonly` bhi list me ho to usko tick karne ki zaroorat
   nahi — `drive.readonly` us se bada hai, sab cover karta hai)
9. Test users: **+ Add users** → **apna hi Gmail** daalo → **Save**
10. 🔴 **Zaroori:** *Publishing status* ko **Testing** se **Publish app** karo
    (Production).
    **Kyun:** Testing mode me Google har 7 din baad refresh token mar deta hai.
    Production me token na expire hota hai. Personal use ke liye Google
    verification nahi chahiye — "unverified app" warning ek baar aayegi, bas
    *Advanced → Go to gdrive-bot* karke aage badh jao.

## C. OAuth client ID — yahan galti hoti hai 🔴

**Do tarah ke client hote hain aur dono ka redirect system alag hai.** Playground
ke liye **Web application** client chahiye, script ke liye **Desktop app**.
Galat client = **`Error 400: redirect_uri_mismatch`** (jo tumhe aaya).

| Raasta | Client type | Kyun |
|---|---|---|
| **D1 — Playground** (phone-friendly) | **Web application** | Playground ka redirect URI (`https://developers.google.com/oauthplayground`) sirf Web client me register hota hai. **Desktop client me ye field hoti hi nahi.** |
| **D2 — Script** (`oauth_helper.py`) | **Desktop app** | Loopback (`http://127.0.0.1:port`) Desktop client me pehle se allowed hota hai |

> Ek hi project me **dono** client bana sakte ho — koi problem nahi. Jo raasta
> chuna, wahi client banao.

### C. Client banao

11. ☰ → **APIs & Services** → **Credentials** → **+ Create credentials** →
    **OAuth client ID**
12. **Application type:**
    * Playground (D1) chahiye → **Web application**
    * Script (D2) chahiye → **Desktop app**
13. (sirf **Web application**) → **Authorized redirect URIs** → **+ Add URI** →
    yeh exactly paste karo (aage-peeche slash nahi):
    ```
    https://developers.google.com/oauthplayground
    ```
    → **Create**
14. **Download JSON** — andar `client_id` + `client_secret` hote hain.
    (Ya page par seedha dikh rahe hote hain, copy kar lo.)

---

## D. Refresh token (3 min) — phone se bhi ho jayega

### D1. OAuth Playground (sabse aasan, koi install nahi) ✅ phone pe bhi

1. Kholo: **https://developers.google.com/oauthplayground**
2. Top-right **⚙️ gear** (settings) → **Use your own OAuth credentials** tick karo
3. `OAuth Client ID` aur `OAuth Client secret` paste karo — **Web application**
   client wale (Step C)
4. Left side list me **Drive API v3** expand karo →
   `https://www.googleapis.com/auth/drive.readonly` tick karo
   *(scope screen par kuch tick na kiya ho to bhi yahan tick karne se kaam ho
   jata hai — yeh Playground ka apna scope selector hai)*
5. **Authorize APIs** dabao → apna Gmail account chuno →
   *Advanced → Go to gdrive-bot (unsafe)* → **Allow**
6. **Step 2** → **Exchange authorization code for tokens** dabao
7. **Refresh token** copy karo — `1//` se shuru hota hai. **Access token nahi**
   (wo 1 ghante me mar jaata hai).

> ✅ Abhi 400 error aaya tha — uski wajah: Desktop client ke saath Playground
> use kiya gaya tha. **Web application** client banao (Step C13 me redirect URI
> add karke) → Playground me wo client ID/secret daalo → error chala jayega.
> Jo Desktop client ban chuka hai usse koi nuksan nahi, use chhod do.

### D2. Script se (laptop, ya Colab/Replit cell)

```bash
python oauth_helper.py                  # browser khud khulega, token print hoga
python oauth_helper.py --manual         # browser kisi doosre device par ho to
python oauth_helper.py --full           # /quotafix bhi chahiye to
```

`--manual` me **code nahi** milta (Google ne wo "code dikhane wala" flow Oct 2022
me hata diya). Wahan aisa hota hai:

1. Script ek link print karega → phone/laptop me kholo → **Allow**
2. Google tumhe `http://127.0.0.1:8765/?code=4/0AX…` par bhejega — **page load
   nahi hoga**. ✅ Yeh normal hai.
3. **Address bar ka poora URL copy karo** (jisme `code=` hai) aur terminal me
   paste kar do — bas.

> Isi liye phone-only + Playground raasta sabse aasan hai: wahan koi copy-paste
> of URLs nahi, seedha token mil jata hai.

---

## E. Ab tumhare paas yeh 3 lines hain

```
GOOGLE_CLIENT_ID=123-abc.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
GOOGLE_REFRESH_TOKEN=1//0gxxxxxxxxxxxxxxxx
```

Yeh hi Render/Railway ke **Variables** tab me daalni hain (Step 1 / Step 2 me
bataya hai). Kahin bhi commit mat karo, kisi ko mat bhejo.

---

## F. Verify (30 sec, deploy ke baad)

Deploy hone ke baad Telegram me:

* `/auth` → account email dikhna chahiye. Dikh gaya = private files ready.
* `/doctor` → har line ✅ honi chahiye.

### Common errors

| Message | Matlab | Fix |
|---|---|---|
| **`Error 400: redirect_uri_mismatch`** | Playground ke saath **Desktop** client use ho raha hai, ya Web client me redirect URI add nahi hui | **Web application** client banao → Authorized redirect URIs me `https://developers.google.com/oauthplayground` add karo → Playground me wahi client ID/secret daalo (Guide Step C) |
| `Error 400: invalid_request` / "Missing required parameter" | URL adhoora copy hua | Link dobara poora copy karo, ya Playground use karo |
| `invalid_grant` | Token expired/revoked, ya app Testing mode me tha | Consent screen **Publish app** karo, phir D1/D2 dobara |
| `access_denied` | Tumhara email test users me nahi | Step B.9 |
| `Google hasn't verified this app` | Normal | *Advanced → Go to (unsafe)* |
| `insufficientPermissions` / 403 | `drive.readonly` scope grant nahi hua (consent screen par tick nahi kiya) | Playground (D1.5) se scope tick karke dobara token lo — `DRIVE_SCOPE=readonly` par wapas |
| `/auth` me "Public-only mode" | 3 env vars host par nahi pahunche | Variables tab check karo, redeploy |

---

**Agla step:** [`RAILWAY_STEP_BY_STEP.md`](RAILWAY_STEP_BY_STEP.md) ya
[`RENDER_STEP_BY_STEP.md`](RENDER_STEP_BY_STEP.md) kholo.
