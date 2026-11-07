#!/bin/sh
# Smoke test: start APP_ID with APK under Xvfb, wait for a window, scan the log.
# Usage: smoke.sh APP_ID APK [SECONDS=90]
# Exit 0 = window appeared and the log has no fatal marker, 1 = failure (log path on stderr).
set -u

[ $# -ge 2 ] || { echo "usage: $0 APP_ID APK [SECONDS]" >&2; exit 2; }
app=$1
apk=$2
secs=${3:-90}
disp=${SMOKE_DISPLAY:-:99}
logdir=$HOME/.var/app/$app/data/logs

if [ -e "/tmp/.X11-unix/X${disp#:}" ]; then
    echo "smoke: display $disp already in use" >&2
    exit 1
fi

work=$(mktemp -d) || exit 1
mkdir -p "$logdir"
touch "$work/start"
xpid=
apid=

cleanup() {
    flatpak kill "$app" 2>/dev/null
    [ -n "$apid" ] && kill "$apid" 2>/dev/null
    [ -n "$xpid" ] && { kill "$xpid" 2>/dev/null; wait "$xpid" 2>/dev/null; }
    rm -rf "$work"
}
trap cleanup EXIT

Xvfb "$disp" -screen 0 1280x900x24 >/dev/null 2>&1 &
xpid=$!
i=0
while [ ! -S "/tmp/.X11-unix/X${disp#:}" ] && [ "$i" -lt 20 ]; do sleep 0.5; i=$((i + 1)); done

# --nosocket=wayland/--socket=x11: a host with a Wayland socket would otherwise not share X11
DISPLAY=$disp flatpak run --nosocket=wayland --socket=x11 --env=GDK_BACKEND=x11 --file-forwarding "$app" @@ "$apk" @@ >"$work/run.out" 2>&1 &
apid=$!

if ! DISPLAY=$disp timeout "$secs" xdotool search --sync --onlyvisible --name . >/dev/null 2>&1; then
    echo "smoke: no window within ${secs}s" >&2
    tail -n 20 "$work/run.out" >&2
    exit 1
fi
# let the app run a moment so early crashes reach the log
sleep 5

log=$(find "$logdir" -type f -newer "$work/start" -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2-)
if [ -z "$log" ]; then
    echo "smoke: no log written under $logdir" >&2
    exit 1
fi
if grep -Eq 'FATAL EXCEPTION|Fatal signal|^# apk-starter: signal=' "$log"; then
    echo "smoke: fatal marker in log: $log" >&2
    grep -En 'FATAL EXCEPTION|Fatal signal|^# apk-starter: signal=' "$log" | head -n 5 >&2
    exit 1
fi
echo "smoke: ok, log: $log" >&2
exit 0
