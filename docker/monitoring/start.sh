#!/bin/sh
# Runs Loki, Prometheus, Alloy and Grafana in the monitoring container (docker/monitoring/Dockerfile), each output
# line prefixed with the tool's name. If one of them stops on its own, the others are stopped and the script exits 1,
# so Docker restarts the whole container; on SIGTERM or SIGINT all four stop and the script exits 0.
set -u

# Runtime settings, never part of the image.
export GF_SECURITY_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-admin}"
export GF_AUTH_ANONYMOUS_ENABLED="${GRAFANA_ANONYMOUS:-true}"

STOP_WAIT_S=20
FIFOS=$(mktemp -d)
NAMES=""

say() { echo "[monitoring] $*"; }

# start NAME COMMAND...: run COMMAND in the background with its output prefixed by [NAME]; its pid goes in PID_NAME.
start() {
    name=$1
    shift
    mkfifo "$FIFOS/$name"
    sed -u "s/^/[$name] /" < "$FIFOS/$name" &
    "$@" > "$FIFOS/$name" 2>&1 &
    eval "PID_$name=$!"
    NAMES="$NAMES $name"
    say "started $name (pid $!)"
}

pid_of() { eval "echo \$PID_$1"; }

running() { kill -0 "$(pid_of "$1")" 2>/dev/null; }

# stop_all CODE: TERM to every tool still running, KILL after STOP_WAIT_S seconds, then exit CODE.
stop_all() {
    trap '' TERM INT
    for name in $NAMES; do
        running "$name" && kill -TERM "$(pid_of "$name")" 2>/dev/null
    done
    waited=0
    while [ "$waited" -lt "$STOP_WAIT_S" ]; do
        left=""
        for name in $NAMES; do
            running "$name" && left="$left $name"
        done
        [ -z "$left" ] && break
        sleep 1
        waited=$((waited + 1))
    done
    for name in $NAMES; do
        if running "$name"; then
            say "$name did not stop within ${STOP_WAIT_S}s; killing it"
            kill -KILL "$(pid_of "$name")" 2>/dev/null
        fi
    done
    rm -rf "$FIFOS"
    say "stopped"
    exit "$1"
}

trap 'say "stop requested"; stop_all 0' TERM INT

start loki loki -config.file=/etc/loki/loki-config.yml
start prometheus prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus \
    --storage.tsdb.retention.time=15d
start alloy alloy run --server.http.listen-addr=0.0.0.0:12345 --storage.path=/var/lib/alloy/data \
    /etc/alloy/config.alloy
start grafana /run.sh

# Check every second; sleep in the background so a stop signal is handled at once.
while :; do
    for name in $NAMES; do
        if ! running "$name"; then
            wait "$(pid_of "$name")"
            say "$name stopped by itself (exit $?); stopping the others so the container restarts"
            stop_all 1
        fi
    done
    sleep 1 &
    wait $!
done
