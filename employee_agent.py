"""
EmpMon V8 - EMPLOYEE AGENT
===========================
Runs silently on each employee PC.
Tracks login/logout/app usage and sends data to the central server via HTTP.

SETUP (on each employee PC):
  1. Install Python: https://python.org
  2. pip install requests pywin32 psutil
  3. Set SERVER_URL below to your IT admin PC's IP address.
  4. Run: python employee_agent.py
  5. OR add to Windows Task Scheduler to start at login (see DEPLOY.bat).

WHAT IT DOES:
  - Records LOGIN when Windows session starts.
  - Records LOGOUT on lock/logoff/shutdown/screen-off.
  - Tracks which app is in foreground + active/idle state.
  - Sends a heartbeat every 5 minutes so the dashboard stays fresh.
  - Retries failed sends with a local queue (works if server is down temporarily).
  - Runs silently in the background — no window shown.
"""

import os, csv, socket, getpass, subprocess, sys, time, ctypes, json, threading
from datetime import datetime
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("ERROR: requests not installed. Run: pip install requests")

try:
    import win32gui, win32process
    HAS_WIN32GUI = True
except ImportError:
    HAS_WIN32GUI = False

try:
    import win32api, win32con, win32ts
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ══════════════════════════════════════════════════════════
#  CHANGE THIS TO YOUR SERVER PC'S IP ADDRESS
SERVER_URL = "http://192.168.1.100:5000"   # ← Edit this
# ══════════════════════════════════════════════════════════

SCRIPTS_DIR      = r"C:\EmpMonitor"
QUEUE_FILE       = os.path.join(SCRIPTS_DIR, "send_queue.jsonl")
DBG_FILE         = os.path.join(SCRIPTS_DIR, "debug_v8.txt")
LOC_CACHE_FILE   = os.path.join(SCRIPTS_DIR, "last_location_v8.txt")

HEARTBEAT_SEC    = 300     # send heartbeat every 5 minutes
APP_POLL_SEC     = 5       # poll foreground app every 5 seconds
IDLE_THRESH_SEC  = 300     # 5 min no input = idle
MAX_SEGMENT_SEC  = 300     # flush app segment after 5 min max
QUEUE_FLUSH_SEC  = 30      # try to flush queue every 30 seconds
DEDUP_SEC        = 120     # ignore duplicate events within 2 minutes

os.makedirs(SCRIPTS_DIR, exist_ok=True)

USER = getpass.getuser()
PC   = socket.gethostname()

_last_event_times = {}
_loc_cache = ("N/A", "N/A", "N/A", "N/A", "N/A", "N/A")
_mutex_handle = None


# ── Single instance ───────────────────────────────────────
def ensure_single_instance():
    global _mutex_handle
    try:
        name = f"Global\\EmpMonV8Agent_{USER}"
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, name)
        if ctypes.windll.kernel32.GetLastError() == 183:
            sys.exit(0)
    except Exception:
        pass


# ── Logging ───────────────────────────────────────────────
def log(m):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(DBG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {m}\n")
    except Exception:
        pass


# ── Location ──────────────────────────────────────────────
def load_loc_cache():
    global _loc_cache
    try:
        if os.path.exists(LOC_CACHE_FILE):
            parts = open(LOC_CACHE_FILE).read().strip().split("|")
            if len(parts) == 6:
                _loc_cache = tuple(parts)
    except Exception:
        pass


def save_loc_cache(loc):
    try:
        with open(LOC_CACHE_FILE, "w") as f:
            f.write("|".join(str(x) for x in loc))
    except Exception:
        pass


def get_serial():
    bad = {"", "none", "n/a", "to be filled by o.e.m.", "system serial number",
           "default string", "not specified", "0", "00000000"}
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-WmiObject -Class Win32_BIOS).SerialNumber"],
            capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        s = r.stdout.strip()
        if s and s.lower() not in bad:
            return s
    except Exception:
        pass
    try:
        r = subprocess.run(["wmic", "bios", "get", "serialnumber", "/value"],
                           capture_output=True, text=True, timeout=4,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        for line in r.stdout.splitlines():
            if "=" in line:
                s = line.split("=", 1)[1].strip()
                if s and s.lower() not in bad:
                    return s
    except Exception:
        pass
    return "N/A"


def get_location():
    global _loc_cache
    ip = "N/A"
    try:
        ip = requests.get("https://ipinfo.io/ip", timeout=4).text.strip()
    except Exception:
        pass

    try:
        d = requests.get("http://ip-api.com/json", timeout=6).json()
        if d.get("status") == "success" and d.get("city", "") not in ("", "N/A"):
            result = (d.get("query", ip), d["city"],
                      d.get("regionName", "N/A"), d.get("countryCode", "IN"),
                      str(d.get("lat", "N/A")), str(d.get("lon", "N/A")))
            _loc_cache = result
            save_loc_cache(result)
            return result
    except Exception:
        pass

    try:
        d = requests.get("https://ipinfo.io/json", timeout=6).json()
        loc = d.get("loc", "0,0").split(",")
        result = (d.get("ip", ip), d.get("city", "N/A"),
                  d.get("region", "N/A"), d.get("country", "IN"),
                  loc[0] if len(loc) > 1 else "N/A",
                  loc[1] if len(loc) > 1 else "N/A")
        if result[1] not in ("N/A", ""):
            _loc_cache = result
            save_loc_cache(result)
            return result
    except Exception:
        pass

    return (_loc_cache[0] if _loc_cache[0] != "N/A" else ip,) + _loc_cache[1:]


# ── HTTP sender with offline queue ────────────────────────
def _enqueue(payload_type, data):
    """Write to local queue if send fails."""
    try:
        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": payload_type, "data": data}) + "\n")
    except Exception as e:
        log(f"Queue write err: {e}")


def send_event(event_data):
    """POST a single event. Queues locally if server is unreachable."""
    if not HAS_REQUESTS:
        return
    try:
        r = requests.post(f"{SERVER_URL}/api/event", json=event_data, timeout=8)
        if r.status_code == 200:
            log(f"Sent: {event_data.get('event')} → server OK")
            return
        log(f"Server error {r.status_code}: {r.text[:80]}")
    except Exception as e:
        log(f"Send failed (queued): {e}")
    _enqueue("event", event_data)


def send_app_event(app_data):
    """POST a single app segment. Queues locally if server is unreachable."""
    if not HAS_REQUESTS:
        return
    try:
        r = requests.post(f"{SERVER_URL}/api/app_event", json=app_data, timeout=8)
        if r.status_code == 200:
            return
    except Exception:
        pass
    _enqueue("app_event", app_data)


def send_heartbeat():
    """Lightweight keep-alive so dashboard shows accurate online status."""
    if not HAS_REQUESTS:
        return
    ip, city, reg, coun, lat, lon = _loc_cache
    try:
        requests.post(f"{SERVER_URL}/api/heartbeat", json={
            "username": USER, "computer": PC,
            "serial":   SERIAL,
            "ip": ip, "city": city, "region": reg, "country": coun
        }, timeout=6)
    except Exception:
        pass


def flush_queue():
    """Retry sending queued events."""
    if not os.path.exists(QUEUE_FILE):
        return
    if not HAS_REQUESTS:
        return

    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return

    if not lines:
        return

    remaining = []
    events    = []
    app_events = []

    for line in lines:
        try:
            entry = json.loads(line.strip())
            if entry["type"] == "event":
                events.append(entry["data"])
            elif entry["type"] == "app_event":
                app_events.append(entry["data"])
        except Exception:
            pass

    if events or app_events:
        try:
            r = requests.post(f"{SERVER_URL}/api/batch", json={
                "events": events, "app_events": app_events
            }, timeout=15)
            if r.status_code == 200:
                result = r.json()
                log(f"Queue flushed: {result.get('inserted', 0)} items sent")
                try:
                    os.remove(QUEUE_FILE)
                except Exception:
                    pass
                return
        except Exception as e:
            log(f"Queue flush failed: {e}")


# ── Event recorder ────────────────────────────────────────
def record_event(event_name, fetch_location=True):
    now = datetime.now()
    last = _last_event_times.get(event_name)
    if last and (now - last).total_seconds() < DEDUP_SEC:
        log(f"Dedup skip: {event_name}")
        return
    _last_event_times[event_name] = now

    if fetch_location and "login" in event_name.lower():
        ip, city, reg, coun, lat, lon = get_location()
    else:
        ip, city, reg, coun, lat, lon = _loc_cache

    data = {
        "date":     now.strftime("%Y-%m-%d"),
        "time":     now.strftime("%H:%M:%S"),
        "event":    event_name,
        "username": USER,
        "computer": PC,
        "serial":   SERIAL,
        "ip":       ip,
        "city":     city,
        "region":   reg,
        "country":  coun,
        "lat":      lat,
        "lon":      lon,
    }
    threading.Thread(target=send_event, args=(data,), daemon=True).start()
    log(f"Event: {event_name} @ {city},{coun}")


# ── App watcher ───────────────────────────────────────────
def idle_seconds():
    try:
        class LII(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]
        lii = LII(); lii.cbSize = ctypes.sizeof(LII)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        tick = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (tick - lii.dwTime) / 1000.0)
    except Exception:
        return 0.0


def get_foreground_app():
    if not HAS_WIN32GUI:
        return ("(unknown)", "(no pywin32)")
    try:
        hwnd  = win32gui.GetForegroundWindow()
        if not hwnd:
            return ("(none)", "(no active window)")
        title = win32gui.GetWindowText(hwnd) or "(no title)"
        app   = "(unknown)"
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if HAS_PSUTIL and pid:
                app = psutil.Process(pid).name()
        except Exception:
            pass
        return (app, title)
    except Exception:
        return ("(unknown)", "(unknown)")


def app_watcher_loop():
    cur = None
    while True:
        try:
            now   = datetime.now()
            state = "idle" if idle_seconds() >= IDLE_THRESH_SEC else "active"
            app, title = get_foreground_app()
            if len(title) > 150:
                title = title[:150] + "..."

            key = (app, title, state)

            if cur is None:
                cur = {"app": app, "title": title, "state": state,
                       "start": now, "end": now}
            elif (cur["app"], cur["title"], cur["state"]) != key:
                cur["end"] = now
                _flush_app_segment(cur)
                cur = {"app": app, "title": title, "state": state,
                       "start": now, "end": now}
            else:
                cur["end"] = now
                if (cur["end"] - cur["start"]).total_seconds() >= MAX_SEGMENT_SEC:
                    _flush_app_segment(cur)
                    cur = {"app": app, "title": title, "state": state,
                           "start": now, "end": now}
        except Exception as e:
            log(f"App loop err: {e}")
        time.sleep(APP_POLL_SEC)


def _flush_app_segment(seg):
    if not seg:
        return
    dur_s = int((seg["end"] - seg["start"]).total_seconds())
    if dur_s < 2:
        return
    data = {
        "date":         seg["start"].strftime("%Y-%m-%d"),
        "start_time":   seg["start"].strftime("%H:%M:%S"),
        "end_time":     seg["end"].strftime("%H:%M:%S"),
        "username":     USER,
        "computer":     PC,
        "app":          seg["app"],
        "window_title": seg["title"],
        "duration":     dur_s,
        "state":        seg["state"],
    }
    threading.Thread(target=send_app_event, args=(data,), daemon=True).start()


# ── Heartbeat thread ──────────────────────────────────────
def heartbeat_loop():
    time.sleep(60)
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log(f"Heartbeat err: {e}")
        time.sleep(HEARTBEAT_SEC)


# ── Queue flush thread ────────────────────────────────────
def queue_flush_loop():
    time.sleep(10)
    while True:
        try:
            flush_queue()
        except Exception as e:
            log(f"Queue flush loop err: {e}")
        time.sleep(QUEUE_FLUSH_SEC)


# ── Windows session watcher ───────────────────────────────
def run_session_watcher():
    if not HAS_WIN32:
        log("pywin32 not installed — session events (lock/unlock/logoff) won't be tracked.")
        return

    WM_WTSSESSION_CHANGE = 0x02B1
    WTS_SESSION_LOCK     = 0x7
    WTS_SESSION_UNLOCK   = 0x8
    WTS_SESSION_LOGOFF   = 0x6
    WM_QUERYENDSESSION   = 0x0011
    WM_ENDSESSION        = 0x0016
    WM_POWERBROADCAST    = 0x0218
    PBT_POWERSETTINGCHANGE = 0x8013

    MONITOR_GUID = (ctypes.c_byte * 16)(*[
        0x56, 0x95, 0xe6, 0x6f, 0x4a, 0x70, 0xa0, 0x47,
        0x8f, 0x24, 0xc2, 0x8d, 0x93, 0x6f, 0xda, 0x47
    ])

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_WTSSESSION_CHANGE:
            if   wparam == WTS_SESSION_LOCK:
                threading.Thread(target=record_event, args=("LOGOUT(lock)", False), daemon=True).start()
            elif wparam == WTS_SESSION_UNLOCK:
                threading.Thread(target=record_event, args=("LOGIN(unlock)",), daemon=True).start()
            elif wparam == WTS_SESSION_LOGOFF:
                threading.Thread(target=record_event, args=("LOGOUT(logoff)", False), daemon=True).start()
        elif msg == WM_QUERYENDSESSION:
            record_event("LOGOUT(shutdown)", False)
            return 1
        elif msg == WM_ENDSESSION:
            return 0
        elif msg == WM_POWERBROADCAST and wparam == PBT_POWERSETTINGCHANGE:
            try:
                class PBS(ctypes.Structure):
                    _fields_ = [("PowerSetting", ctypes.c_byte * 16),
                                ("DataLength", ctypes.c_ulong),
                                ("Data", ctypes.c_ulong)]
                pbs = PBS.from_address(lparam)
                if bytes(pbs.PowerSetting) == bytes(MONITOR_GUID):
                    if pbs.Data == 0:
                        threading.Thread(target=record_event, args=("LOGOUT(screen-off)", False), daemon=True).start()
                    elif pbs.Data == 1:
                        threading.Thread(target=record_event, args=("LOGIN(screen-on)",), daemon=True).start()
            except Exception:
                pass
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc   = wnd_proc
    wc.lpszClassName = "EmpMonV8Agent"
    wc.hInstance     = win32api.GetModuleHandle(None)

    try:
        win32gui.RegisterClass(wc)
    except Exception as e:
        log(f"RegisterClass: {e}"); return

    hwnd = win32gui.CreateWindow(
        wc.lpszClassName, "EmpMonV8", 0, 0, 0, 0, 0,
        win32con.HWND_MESSAGE, 0, wc.hInstance, None
    )
    if not hwnd:
        log("CreateWindow failed"); return

    try:
        win32ts.WTSRegisterSessionNotification(hwnd, win32ts.NOTIFY_FOR_THIS_SESSION)
    except Exception as e:
        log(f"WTS register: {e}")

    try:
        ctypes.windll.user32.RegisterPowerSettingNotification(
            ctypes.c_void_p(hwnd), ctypes.byref(MONITOR_GUID), ctypes.c_ulong(0))
    except Exception:
        pass

    log("Session watcher active (lock/unlock/logoff/shutdown/screen)")
    win32gui.PumpMessages()


# ── Idle watcher ──────────────────────────────────────────
def idle_watcher_loop():
    IDLE_MS  = 5 * 60 * 1000
    was_idle = False
    while True:
        try:
            class LII(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]
            lii = LII(); lii.cbSize = ctypes.sizeof(LII)
            ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
            idle_ms = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            if idle_ms > IDLE_MS and not was_idle:
                record_event("LOGOUT(idle)", False)
                was_idle = True
            elif idle_ms < 10000 and was_idle:
                record_event("LOGIN(idle-resume)")
                was_idle = False
        except Exception as e:
            log(f"Idle watcher: {e}")
        time.sleep(20)


# ── MAIN ──────────────────────────────────────────────────
if __name__ == "__main__":
    ensure_single_instance()

    log(f"EmpMon V8 Agent starting. User={USER} PC={PC}")
    log(f"Server: {SERVER_URL}")

    load_loc_cache()
    SERIAL = get_serial()
    log(f"Serial: {SERIAL}")

    # Verify server is reachable
    if HAS_REQUESTS:
        try:
            r = requests.get(f"{SERVER_URL}/api/status", timeout=5)
            log(f"Server reachable: {r.json()}")
        except Exception as e:
            log(f"Server not reachable at startup (will retry): {e}")

    # Record LOGIN
    record_event("LOGIN")

    # Start background threads
    threading.Thread(target=app_watcher_loop,   daemon=True).start()
    threading.Thread(target=heartbeat_loop,      daemon=True).start()
    threading.Thread(target=queue_flush_loop,    daemon=True).start()
    threading.Thread(target=idle_watcher_loop,   daemon=True).start()

    log("All background threads started.")

    # Run session watcher on main thread (needs Windows message pump)
    run_session_watcher()

    # Fallback: if no win32 available, just keep alive
    log("No win32 session watcher — keeping alive with heartbeats only.")
    while True:
        time.sleep(60)
