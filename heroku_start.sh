#!/usr/bin/env bash
# heroku_start.sh — start command for Heroku's *container* stack.
#
# `heroku stack:set container` ignores the Procfile (Dockerfile CMD wins), so
# point the dyno at this script instead:
#   heroku ps:scale worker=0
#   heroku ps:scale worker=1        # after setting the start command below
#   heroku config:set START_CMD="bash heroku_start.sh"
#
# Why not the default CMD? Heroku's filesystem is ephemeral and NOTHING is
# mounted at /data, so we redirect the ledger + scratch space to $HOME (the only
# writable place) and warn loudly that the resume ledger resets on every dyno
# cycle — that is a platform limitation, not a bot bug.
set -euo pipefail

HOME_DIR="${HOME:-/app}"
export DATA_DIR="${DATA_DIR:-$HOME_DIR/data}"
export TEMP_DIR="${TEMP_DIR:-$DATA_DIR/tmp}"
export JOBS_DB="${JOBS_DB:-$DATA_DIR/jobs.sqlite3}"
mkdir -p "$TEMP_DIR" "$DATA_DIR/logs"

echo "──────────────────────────────────────────────────────────────"
echo " data dir : $DATA_DIR"
echo " jobs db  : $JOBS_DB"
echo " NOTE: Heroku has an ephemeral filesystem. The 'already sent'"
echo "       ledger resets on every restart/redeploy. Files are still"
echo "       sent correctly — you just lose resume + dedupe."
echo "       For a persistent ledger, use Render (disk) or a GCE VM."
echo "──────────────────────────────────────────────────────────────"

exec python railway_setup.py && exec python bot.py
