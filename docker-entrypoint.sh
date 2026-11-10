#!/bin/sh
# docker-entrypoint.sh — fix volume ownership, then start the bot.
#
# The problem this solves: Railway (and Fly, Render, most container hosts) mount
# the persistent volume fresh on every boot with root:root 0755 ownership. Our
# process runs as `botuser` (uid 1000), so the first thing it does — open
# /data/jobs.sqlite3 — fails with:
#
#     sqlite3.OperationalError: unable to open database file
#
# and the bot crash-loops. Running as root the whole time would fix the perms
# but means a Telegram bot with your Google tokens runs privileged, which is
# unnecessary risk.
#
# So: start as root, chown the volume once, drop to botuser, exec the bot.
# Idempotent, and harmless if the volume is already owned correctly.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
TEMP_DIR="${TEMP_DIR:-$DATA_DIR/tmp}"
APP_USER="${APP_USER:-botuser}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR" "$TEMP_DIR" "$DATA_DIR/logs" 2>/dev/null || true
    if ! chown -R "$APP_USER:$APP_USER" "$DATA_DIR" 2>/dev/null; then
        echo "! could not chown $DATA_DIR (read-only mount?). Continuing as root." >&2
    fi

    # Hand over to the unprivileged user. `exec su-exec`/`gosu` style is not in
    # slim images, so use setpriv (part of util-linux, present in debian slim).
    if command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups \
            sh -c 'python railway_setup.py && exec python bot.py'
    fi
    # Fallback: busybox/su syntax differs per base image; su is universally there.
    if command -v su >/dev/null 2>&1; then
        exec su "$APP_USER" -s /bin/sh -c 'python railway_setup.py && exec python bot.py'
    fi
    echo "! no way to drop privileges found; running as root" >&2
fi

python railway_setup.py && exec python bot.py
