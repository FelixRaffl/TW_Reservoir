import threading, subprocess, json, time, sys

# ----------------- Einstellungen -----------------
POLL_INTERVAL_S   = 0.05   # wie oft Abfragen (50 ms)
DEBOUNCE_S        = 0.03   # Entprellzeit (30 ms)
API_TIMEOUT_S     = 0.5
SMS_TIMEOUT_S     = 3.0
DOOR_DELAY_S      = 120    # 2 Minuten
ACTIVE_LOW        = True   # dwi0/dwi1: 0 = aktiv (geschlossen/gedrückt)

NUMBERS = ["+393894442477"]

# ----------------- Zustände -----------------
prev_dwi0 = None  # Türkontakt
prev_dwi1 = None  # Taster
taster3_flag = False
door_closed = False
send_sms_door = False
door_timer = None
last_read_time = {"dwi0": 0.0, "dwi1": 0.0}
last_read_val  = {"dwi0": None, "dwi1": None}

# ----------------- Helfer -----------------
def run_json(cmd, timeout):
    """Startet cmd, liefert geparstes JSON oder wirft Exception."""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)} :: {p.stderr.strip()}")
    if not p.stdout.strip():
        raise RuntimeError(f"empty stdout: {' '.join(cmd)}")
    return json.loads(p.stdout)

def get_dwi_value(io_id):
    """Liest dwiX, mit leichtem Cache & Entprellung."""
    now = time.monotonic()
    if now - last_read_time[io_id] < DEBOUNCE_S and last_read_val[io_id] is not None:
        return last_read_val[io_id]
    try:
        resp = run_json(["/sbin/api", "GET", f"/io/{io_id}/status"], API_TIMEOUT_S)
        data = resp["http_body"]["data"]
        raw = int(data["value"])  # 0 oder 1
        val = bool(raw)
        if ACTIVE_LOW:
            val = not val
        last_read_time[io_id] = now
        last_read_val[io_id] = val
        return val
    except Exception as e:
        print(f"[WARN] get_dwi_value({io_id}): {e}", file=sys.stderr)
        return last_read_val[io_id]

def send_sms(number, text):
    payload = {"number": number, "text": text, "validate": False, "async": False}
    try:
        r = subprocess.run(
            ["ubus", "call", "gsm.modem0", "send_sms", json.dumps(payload)],
            capture_output=True, text=True, timeout=SMS_TIMEOUT_S
        )
        if r.returncode != 0:
            print(f"[ERR] SMS zu {number} fehlgeschlagen: {r.stderr.strip()}", file=sys.stderr)
        else:
            print(f"[OK] SMS an {number}: {text}")
    except subprocess.TimeoutExpired:
        print(f"[ERR] SMS Timeout zu {number}", file=sys.stderr)

def schedule_door_timer():
    """Startet den 2-Minuten-Timer neu (idempotent)."""
    global door_timer
    if door_timer is not None and door_timer.is_alive():
        door_timer.cancel()
    door_timer = threading.Timer(DOOR_DELAY_S, door_timer_elapsed)
    door_timer.daemon = True
    door_timer.start()
    print("door opened ... 2 min timer started")

def door_timer_elapsed():
    """Wird nach 2 Minuten aufgerufen."""
    global taster3_flag, send_sms_door

    print("Door timer elapsed (2 minutes passed)")  # <-- hinzugefügt

    if not taster3_flag:
        send_sms_door = True

    taster3_flag = False

# ----------------- Hauptlogik -----------------
def main():
    global prev_dwi0, prev_dwi1, taster3_flag, send_sms_door, door_closed

    print("Starting loop. Ctrl+C to exit.")
    try:
        while True:
            dwi0 = get_dwi_value("dwi0")  # Türkontakt
            dwi1 = get_dwi_value("dwi1")  # Taster

            if prev_dwi0 is None: prev_dwi0 = dwi0
            if prev_dwi1 is None: prev_dwi1 = dwi1

            # ---- Taster3 gedrückt (steigende Flanke) ----
            if dwi1 and not prev_dwi1:
                taster3_flag = True
                print("Taster3 gedrückt (Flag gesetzt)")

            # ---- Tür öffnet (fallende Flanke) ----
            door_war_zu = prev_dwi0
            door_jetzt_zu = dwi0
            if door_war_zu and not door_jetzt_zu:
                schedule_door_timer()

            # ---- SMS senden falls Timer es fordert ----
            if send_sms_door:
                for num in NUMBERS:
                    send_sms(num, "door was opened")
                send_sms_door = False

            prev_dwi0, prev_dwi1 = dwi0, dwi1
            time.sleep(POLL_INTERVAL_S)

    except KeyboardInterrupt:
        print("\nAbbruch durch Benutzer.")
    finally:
        if door_timer is not None and door_timer.is_alive():
            door_timer.cancel()

if __name__ == "__main__":
    main()
