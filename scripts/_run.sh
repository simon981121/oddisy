#!/bin/bash
# Gemensam körare för cron: lås, timeout, tidsstämplad logg och loggrotation.
# Användning: _run.sh <namn> <skript.py>   (anropas av run_main.sh, run_clv.sh och run_results.sh)
# Skriver bara till logs/<namn>.log, aldrig till stdout, så cron skickar ingen post.
# Måste fungera med macOS /bin/bash 3.2.
# ODDISY_DIR, ODDISY_PYTHON, ODDISY_TIMEOUT och ODDISY_KILL_GRACE finns bara för testerna.

NAME="$1"
SCRIPT="$2"
DIR="${ODDISY_DIR:-/Users/simonsayadi/Dev/oddisy}"
PYTHON="${ODDISY_PYTHON:-/Users/simonsayadi/Dev/oddisy/venv/bin/python}"
TIMEOUT="${ODDISY_TIMEOUT:-600}"         # sekunder för hela körningen
KILL_GRACE="${ODDISY_KILL_GRACE:-10}"    # sekunder mellan TERM och KILL vid timeout
MAX_LOG_BYTES=1048576                     # ca 1 MB, sedan flyttas loggen till <namn>.log.1

if [ -z "$NAME" ] || [ -z "$SCRIPT" ]; then
    echo "Användning: $0 <namn> <skript.py>" >&2
    exit 64
fi

cd "$DIR" || exit 1
LOGDIR="$DIR/logs"
LOG="$LOGDIR/$NAME.log"
LOCK="$LOGDIR/$NAME.lock"
mkdir -p "$LOGDIR" || exit 1

now() {
    date '+%Y-%m-%d %H:%M:%S %z'
}

log() {
    printf '%s [%s] %s\n' "$(now)" "$NAME" "$*" >> "$LOG"
}

stamp() {
    # Tidsstämplar varje rad som skriptet skriver ut, även en sista rad utan radbrytning
    while IFS= read -r line || [ -n "$line" ]; do
        printf '%s [%s] %s\n' "$(now)" "$NAME" "$line"
    done
}

lock_owner_alive() {
    local owner
    owner=$(cat "$LOCK/pid" 2>/dev/null)
    if [ -z "$owner" ]; then
        # Låset kan precis ha skapats utan att pid hunnit skrivas: levande om yngre än 1 minut
        [ -z "$(find "$LOCK" -maxdepth 0 -mmin +1 2>/dev/null)" ]
        return
    fi
    # kill -0 räcker inte: pid kan ha återanvänts av en annan process
    ps -p "$owner" -o command= 2>/dev/null | grep -q "_run.sh"
}

# --- Lås (mkdir är atomiskt) ---
if ! mkdir "$LOCK" 2>/dev/null; then
    if lock_owner_alive; then
        log "Hoppar över: förra körningen pågår fortfarande (pid $(cat "$LOCK/pid" 2>/dev/null))."
        exit 0
    fi
    log "Tar bort gammalt lås, processen lever inte längre."
    rm -rf "$LOCK"
    if ! mkdir "$LOCK" 2>/dev/null; then
        log "Hoppar över: en annan körning tog låset."
        exit 0
    fi
fi
echo $$ > "$LOCK/pid"

pid=""
watchdog=""
signal_run() {
    # Barnens pid tas innan föräldern stoppas (sedan byter de förälder och hittas inte),
    # men föräldern stoppas först så att den inte hinner fortsätta när barnen dör.
    [ -n "$pid" ] || return
    local kids
    kids=$(pgrep -P "$pid" 2>/dev/null)
    kill "-$1" "$pid" 2>/dev/null
    [ -n "$kids" ] && kill "-$1" $kids 2>/dev/null
}
stop_watchdog() {
    if [ -n "$watchdog" ]; then
        # Döda sleep-barnet också, annars lever det kvar tills tiden gått ut
        local kids
        kids=$(pgrep -P "$watchdog" 2>/dev/null)
        kill "$watchdog" 2>/dev/null
        [ -n "$kids" ] && kill $kids 2>/dev/null
        watchdog=""
    fi
}
cleanup() {
    stop_watchdog
    rm -rf "$LOCK"
}
trap cleanup EXIT
trap 'signal_run TERM; log "Avbruten av signal."; exit 143' INT TERM HUP

# --- Rotation (efter låset, så två körningar aldrig roterar samtidigt) ---
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG" "$LOG.1"
fi

if [ ! -x "$PYTHON" ]; then
    log "Fel: hittar inte $PYTHON"
    exit 127
fi

# --- Körning med timeout ---
log "Startar $SCRIPT (pid $$, timeout $TIMEOUT s)"
start=$(date +%s)
fifo="$LOCK/out"
mkfifo "$fifo" || exit 1
stamp < "$fifo" >> "$LOG" &
stamper=$!
"$PYTHON" -u "$SCRIPT" > "$fifo" 2>&1 < /dev/null &
pid=$!

(
    sleep "$TIMEOUT"
    touch "$LOCK/timeout"
    signal_run TERM
    sleep "$KILL_GRACE"
    signal_run KILL
) > /dev/null 2>&1 &
watchdog=$!

wait "$pid"
code=$?
stop_watchdog
if [ -e "$LOCK/timeout" ]; then
    # Ett kvarlevande barnbarn kan hålla fifon öppen, vänta inte på det
    sleep 1
    kill "$stamper" 2>/dev/null
fi
wait "$stamper" 2>/dev/null
duration=$(( $(date +%s) - start ))

if [ -e "$LOCK/timeout" ]; then
    log "TIMEOUT: $SCRIPT stoppades efter $TIMEOUT s."
    code=124
elif [ "$code" -eq 0 ]; then
    log "Klar på $duration s."
elif [ "$code" -eq 2 ]; then
    log "Slut med kod 2 efter $duration s. Kreditskyddet? Se raden \"Avbryter:\" ovan."
else
    log "Slut med felkod $code efter $duration s."
fi
exit "$code"
