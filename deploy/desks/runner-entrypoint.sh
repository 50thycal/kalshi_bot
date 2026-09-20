#!/bin/sh
set -eu
umask 077
# Railway must mount a dedicated persistent volume here for each runner.
if ! mountpoint -q /data; then
    echo "A dedicated persistent volume must be mounted at /data." >&2
    exit 2
fi
if [ "$(id -u)" = 0 ]; then
    mkdir -p /data/model-home /data/runner
    chown desk:desk /data /data/model-home /data/runner
    chmod 700 /data /data/model-home /data/runner
    exec gosu desk "$0" "$@"
fi
if [ "$(id -u)" != 10001 ]; then
    echo "Runner must use its dedicated service identity." >&2
    exit 2
fi
cd /opt/kalshi_bot
exec "$@"
