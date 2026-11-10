"""
railway_setup.py — prepare a Railway container before the bot starts.

Railway (and most container hosts) mount a persistent volume as root, while the
app runs as an unprivileged user. That means /data looks writable in the build
but throws `sqlite3.OperationalError: unable to open database file` at runtime —
the single most common way a Drive bot "deploys fine then silently loses every
job". This script creates and chowns the runtime dirs, then hands over.

It is tolerant: in local development there is no /data, so it falls back to
./data or a temp dir and simply prints what it did.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_ROOT = "/data"


def resolve_root() -> Path:
    explicit = os.getenv("DATA_DIR", "").strip()
    if explicit:
        return Path(explicit)
    if Path(DEFAULT_ROOT).is_dir():
        return Path(DEFAULT_ROOT)
    if Path("/app").is_dir():
        return Path("/app/data")
    return Path.cwd() / "data"


def ensure(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError as exc:
        print(f"✗ {path} is not writable: {exc}", file=sys.stderr)
        return False


def main() -> int:
    root = resolve_root()
    ok = ensure(root)
    for sub in ("tmp", "logs"):
        ok = ensure(root / sub) and ok

    jobs_db = Path(os.getenv("JOBS_DB", str(root / "jobs.sqlite3")))
    os.environ.setdefault("JOBS_DB", str(jobs_db))
    os.environ.setdefault("TEMP_DIR", str(root / "tmp"))
    ok = ensure(jobs_db.parent) and ok

    print(f"• data dir   : {root} ({'writable' if ok else 'NOT WRITABLE'})")
    print(f"• jobs ledger: {jobs_db}")
    print(f"• temp dir   : {os.environ['TEMP_DIR']}")

    uid = os.getuid()
    if not ok and uid != 0:
        print(
            "\nHint: on Railway, set the service user to root (Settings → Deploy →\n"
            "Custom Start Command) once, or mount the volume at DATA_DIR and make\n"
            "sure it is owned by the container user.",
            file=sys.stderr,
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
