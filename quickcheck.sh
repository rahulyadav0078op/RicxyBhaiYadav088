#!/bin/sh
# quickcheck.sh — paste into a host shell (Render/Railway) when doctor.py isn't
# in the image. Self-contained: no imports beyond stdlib, masks secrets.
python3 - <<'EOF'
import os, json, urllib.request as u, urllib.parse as p, urllib.error as e

T = os.environ.get("BOT_TOKEN", "")
print("BOT_TOKEN set:", bool(T), "len", len(T))
try:
    r = u.urlopen(f"https://api.telegram.org/bot{T}/getMe", timeout=20)
    d = json.loads(r.read())
    print("getMe:", d.get("result", {}).get("username"), "id", d.get("result", {}).get("id"))
except e.HTTPError as x:
    print("getMe FAILED:", x.code, x.read().decode()[:200])
try:
    r = u.urlopen(f"https://api.telegram.org/bot{T}/getWebhookInfo", timeout=20)
    d = json.loads(r.read()).get("result", {})
    print("webhook url:", repr(d.get("url", "")), "| pending:", d.get("pending_update_count"),
          "| last_error:", d.get("last_error_message"))
except Exception as x:
    print("webhook check failed:", repr(x))

c = os.environ.get("GOOGLE_CLIENT_ID", "")
s = os.environ.get("GOOGLE_CLIENT_SECRET", "")
rt = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
print("creds set:", bool(c), bool(s), bool(rt))
print("  client_id tail:", c[-32:] or "(missing)", "| secret head:", s[:7] or "(missing)",
      "| token head:", rt[:4] or "(missing)")
body = p.urlencode({"client_id": c, "client_secret": s, "refresh_token": rt,
                    "grant_type": "refresh_token"}).encode()
try:
    r = u.urlopen(u.Request("https://oauth2.googleapis.com/token", data=body,
                  headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=30)
    j = json.loads(r.read())
    print("google: OK | scope:", j.get("scope"))
    at = j["access_token"]
    q = p.urlencode({"q": "'1OoGb2u2v9o9SbO1eACZ10Pa4GzV--SO4' in parents and trashed=false",
                     "fields": "files(name,mimeType)", "pageSize": "5",
                     "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    r = u.urlopen(u.Request("https://www.googleapis.com/drive/v3/files?" + q,
                  headers={"Authorization": "Bearer " + at}), timeout=30)
    f = json.loads(r.read()).get("files", [])
    print("folder entries:", len(f), [x.get("name", "")[:24] for x in f[:5]])
except e.HTTPError as x:
    print("google FAILED:", x.code, x.read().decode()[:300])
except Exception as x:
    print("google check failed:", repr(x))
EOF
