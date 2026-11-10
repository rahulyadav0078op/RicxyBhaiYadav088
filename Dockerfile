# Dockerfile — the bot's runtime image.
#
# Why Docker instead of a plain Python buildpack:
#   * ffmpeg + ffprobe must exist as real binaries (track joining for
#     /streamdl and /merge). Buildpacks make you hope; this apt-installs them
#     and then fails the BUILD if they are missing, not at 2am in production.
#   * the entrypoint fixes volume ownership before dropping privileges, which
#     is what stops the classic "sqlite3.OperationalError: unable to open
#     database file" crash-loop on Railway/Render.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    DATA_DIR=/data \
    TEMP_DIR=/data/tmp \
    JOBS_DB=/data/jobs.sqlite3 \
    APP_USER=botuser \
    PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ffmpeg is required to join Google's separate video/audio stream tracks
# (stream copy, no re-encode). ffprobe comes with it and lets us verify that
# the merged file really contains audio before uploading it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Ship every module the bot imports at runtime (/doctor, self test, CLI tools).
COPY *.py ./
COPY docker-entrypoint.sh ./

# Fail the build early if a module or a binary is missing.
RUN python -c "import bot, gdrive_core, health, healthcheck, media_mux, media_download, railway_setup, oauth_helper, env_check, selftest" \
    && python -c "import media_mux; assert media_mux.available(), 'ffmpeg binary missing'" \
    && python -c "import media_mux; assert media_mux.FFPROBE, 'ffprobe binary missing'" \
    && ffmpeg -version > /dev/null \
    && ffprobe -version > /dev/null
RUN chmod +x docker-entrypoint.sh

# Volume mount point + unprivileged user. The container starts as root only long
# enough to chown a freshly-mounted /data, then drops to botuser — see
# docker-entrypoint.sh for why.
RUN mkdir -p /data/tmp /data/logs \
    && useradd -m -u 1000 botuser \
    && chown -R botuser:botuser /data /app

EXPOSE 8080

ENTRYPOINT ["/app/docker-entrypoint.sh"]
