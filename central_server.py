"""
EmpMon V8 - CENTRAL SERVER
===========================
Receives data from ALL employee PCs via HTTP API.
Stores everything in SQLite.
Serves a live web dashboard showing all employees.

HOW IT WORKS:
  1. Run this on the IT/HR admin PC (or any always-on machine).
  2. Deploy employee_agent.py on each employee PC â€” it POSTs data here.
  3. Open http://THIS-PC-IP:5000 in any browser to see the dashboard.

INSTALL:
  pip install flask

RUN:
  python central_server.py
"""

import os, json, sqlite3, socket
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST).replace(tzinfo=None)
from collections import defaultdict, Counter
from flask import Flask, request, jsonify, render_template_string

# â”€â”€ CONFIG â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
COMPANY      = "W-SAFE REINSURANCE"
PORT         = 5000
DB_PATH      = os.path.join(os.path.dirname(__file__), "empmon.db")
IDLE_MIN     = 10
OFFLINE_MIN  = 30
REFRESH_S    = 60
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

app = Flask(__name__)


# â”€â”€ DATABASE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS raw_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                event       TEXT NOT NULL,
                username    TEXT NOT NULL,
                computer    TEXT NOT NULL,
                serial      TEXT DEFAULT 'N/A',
                ip          TEXT DEFAULT 'N/A',
                city        TEXT DEFAULT 'N/A',
                region      TEXT DEFAULT 'N/A',
                country     TEXT DEFAULT 'IN',
                lat         TEXT DEFAULT 'N/A',
                lon         TEXT DEFAULT 'N/A',
                received_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT NOT NULL,
                start_time   TEXT NOT NULL,
                end_time     TEXT NOT NULL,
                username     TEXT NOT NULL,
                computer     TEXT NOT NULL,
                app          TEXT NOT NULL,
                window_title TEXT DEFAULT '',
                duration_sec INTEGER DEFAULT 0,
                state        TEXT DEFAULT 'active',
                received_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vpn_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                username    TEXT NOT NULL,
                computer    TEXT NOT NULL,
                vpn_on      INTEGER DEFAULT 0,
                software    TEXT DEFAULT '',
                adapter     TEXT DEFAULT '',
                received_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_vpn_user ON vpn_log(username, computer, date);
            CREATE TABLE IF NOT EXISTS usb_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                username    TEXT NOT NULL,
                computer    TEXT NOT NULL,
                drive       TEXT DEFAULT '',
                label       TEXT DEFAULT '',
                size_gb     REAL DEFAULT 0,
                action      TEXT DEFAULT 'connected',
                received_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_usb_user ON usb_log(username, computer, date);
            CREATE TABLE IF NOT EXISTS disk_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT NOT NULL,
                time         TEXT NOT NULL,
                username     TEXT NOT NULL,
                computer     TEXT NOT NULL,
                drive        TEXT DEFAULT 'C:',
                total_gb     REAL DEFAULT 0,
                used_gb      REAL DEFAULT 0,
                free_gb      REAL DEFAULT 0,
                pct_used     REAL DEFAULT 0,
                received_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS browser_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                username    TEXT NOT NULL,
                computer    TEXT NOT NULL,
                domain      TEXT NOT NULL,
                secs        INTEGER DEFAULT 0,
                received_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_browser_user ON browser_log(username, computer, date);
            CREATE INDEX IF NOT EXISTS idx_raw_date     ON raw_log(date, username);
            CREATE INDEX IF NOT EXISTS idx_raw_user     ON raw_log(username, computer);
            CREATE INDEX IF NOT EXISTS idx_app_date     ON app_log(date, username);
            CREATE INDEX IF NOT EXISTS idx_app_user     ON app_log(username, computer);
            CREATE INDEX IF NOT EXISTS idx_disk_user    ON disk_log(username, computer, date);
        """)
    print(f"[DB] Database ready: {DB_PATH}")


# â”€â”€ HELPERS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def fmt_secs(s):
    if not s or s <= 0:
        return "0h 00m 00s"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m:02d}m {sec:02d}s"


def fmt_dec(s):
    return round((s or 0) / 3600, 2)


def parse_duration(val):
    """Accept HH:MM:SS or raw seconds."""
    try:
        v = str(val).strip()
        if ":" in v:
            p = v.split(":")
            return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
        return int(float(v or 0))
    except Exception:
        return 0


# â”€â”€ API ENDPOINTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.route("/api/event", methods=["POST"])
def api_event():
    """Employee PC posts a login/logout event here."""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "msg": "no JSON"}), 400

        required = ["date", "time", "event", "username", "computer"]
        for f in required:
            if not data.get(f):
                return jsonify({"status": "error", "msg": f"missing {f}"}), 400

        now = now_ist().strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            conn.execute("""
                INSERT INTO raw_log
                  (date,time,event,username,computer,serial,ip,city,region,country,lat,lon,received_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data["date"], data["time"], data["event"],
                data["username"], data["computer"],
                data.get("serial", "N/A"),
                data.get("ip", "N/A"),
                data.get("city", "N/A"),
                data.get("region", "N/A"),
                data.get("country", "IN"),
                data.get("lat", "N/A"),
                data.get("lon", "N/A"),
                now
            ))
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


@app.route("/api/app_event", methods=["POST"])
def api_app_event():
    """Employee PC posts an app usage segment here."""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "msg": "no JSON"}), 400

        required = ["date", "start_time", "end_time", "username", "computer", "app"]
        for f in required:
            if not data.get(f):
                return jsonify({"status": "error", "msg": f"missing {f}"}), 400

        now = now_ist().strftime("%Y-%m-%d %H:%M:%S")
        dur_s = parse_duration(data.get("duration", data.get("duration_sec", 0)))

        with get_db() as conn:
            conn.execute("""
                INSERT INTO app_log
                  (date,start_time,end_time,username,computer,app,window_title,duration_sec,state,received_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                data["date"], data["start_time"], data["end_time"],
                data["username"], data["computer"],
                data["app"],
                data.get("window_title", ""),
                dur_s,
                data.get("state", "active"),
                now
            ))
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


@app.route("/api/batch", methods=["POST"])
def api_batch():
    """Send multiple events in one request (reduces network calls)."""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "msg": "no JSON"}), 400

        now = now_ist().strftime("%Y-%m-%d %H:%M:%S")
        inserted = 0
        with get_db() as conn:
            for ev in data.get("events", []):
                try:
                    conn.execute("""
                        INSERT INTO raw_log
                          (date,time,event,username,computer,serial,ip,city,region,country,lat,lon,received_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        ev["date"], ev["time"], ev["event"],
                        ev["username"], ev["computer"],
                        ev.get("serial", "N/A"), ev.get("ip", "N/A"),
                        ev.get("city", "N/A"), ev.get("region", "N/A"),
                        ev.get("country", "IN"), ev.get("lat", "N/A"),
                        ev.get("lon", "N/A"), now
                    ))
                    inserted += 1
                except Exception:
                    pass

            for ae in data.get("app_events", []):
                try:
                    dur_s = parse_duration(ae.get("duration", ae.get("duration_sec", 0)))
                    conn.execute("""
                        INSERT INTO app_log
                          (date,start_time,end_time,username,computer,app,window_title,duration_sec,state,received_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        ae["date"], ae["start_time"], ae["end_time"],
                        ae["username"], ae["computer"], ae["app"],
                        ae.get("window_title", ""), dur_s,
                        ae.get("state", "active"), now
                    ))
                    inserted += 1
                except Exception:
                    pass

        return jsonify({"status": "ok", "inserted": inserted})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    """Lightweight heartbeat â€” keeps employee status fresh without a full event."""
    try:
        data = request.get_json(force=True)
        if not data or not data.get("username"):
            return jsonify({"status": "error"}), 400
        now = now_ist()
        with get_db() as conn:
            conn.execute("""
                INSERT INTO raw_log
                  (date,time,event,username,computer,serial,ip,city,region,country,lat,lon,received_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                "HEARTBEAT",
                data["username"], data.get("computer", "N/A"),
                data.get("serial", "N/A"), data.get("ip", "N/A"),
                data.get("city", "N/A"), data.get("region", "N/A"),
                data.get("country", "IN"), "N/A", "N/A",
                now.strftime("%Y-%m-%d %H:%M:%S")
            ))
            # Store VPN status if sent
            if data.get("vpn"):
                v = data["vpn"]
                conn.execute("""
                    INSERT INTO vpn_log (date,time,username,computer,vpn_on,software,adapter,received_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                    data["username"], data.get("computer","N/A"),
                    1 if v.get("connected") else 0,
                    ", ".join(v.get("software", [])),
                    v.get("adapter",""),
                    now.strftime("%Y-%m-%d %H:%M:%S")
                ))
            # Store browser top sites if sent
            if data.get("browser_sites"):
                for site in data["browser_sites"]:
                    conn.execute("""
                        INSERT INTO browser_log (date,time,username,computer,domain,secs,received_at)
                        VALUES (?,?,?,?,?,?,?)
                    """, (
                        now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                        data["username"], data.get("computer","N/A"),
                        site.get("domain",""), site.get("secs", 0),
                        now.strftime("%Y-%m-%d %H:%M:%S")
                    ))
            # Store remote desktop alert if sent
            if data.get("remote_apps"):
                apps_str = "REMOTE:" + ", ".join(data["remote_apps"])
                conn.execute("""
                    INSERT INTO vpn_log (date,time,username,computer,vpn_on,software,adapter,received_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                    data["username"], data.get("computer","N/A"),
                    1, apps_str, "remote_desktop",
                    now.strftime("%Y-%m-%d %H:%M:%S")
                ))
            # Store USB devices if sent
            if data.get("usb_drives"):
                for u in data["usb_drives"]:
                    conn.execute("""
                        INSERT INTO usb_log (date,time,username,computer,drive,label,size_gb,action,received_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (
                        now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                        data["username"], data.get("computer","N/A"),
                        u.get("drive",""), u.get("label",""), u.get("size_gb",0),
                        "connected", now.strftime("%Y-%m-%d %H:%M:%S")
                    ))
            # Store disk usage if sent
            if data.get("disks"):
                for d in data["disks"]:
                    conn.execute("""
                        INSERT INTO disk_log (date,time,username,computer,drive,total_gb,used_gb,free_gb,pct_used,received_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                        data["username"], data.get("computer", "N/A"),
                        d.get("drive","C:"), d.get("total_gb",0), d.get("used_gb",0),
                        d.get("free_gb",0), d.get("pct_used",0),
                        now.strftime("%Y-%m-%d %H:%M:%S")
                    ))
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


# â”€â”€ DASHBOARD DATA BUILDERS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def get_all_employees_today():
    today = now_ist().strftime("%Y-%m-%d")
    this_month = now_ist().strftime("%Y-%m")

    with get_db() as conn:
        # Get distinct employees (any time)
        emps = conn.execute("""
            SELECT DISTINCT username, computer FROM raw_log ORDER BY username
        """).fetchall()

        rows = []
        online_cnt = idle_cnt = offline_cnt = 0
        total_active_today = 0

        for emp in emps:
            username = emp["username"]
            computer = emp["computer"]

            # Today's raw events
            today_rows = conn.execute("""
                SELECT * FROM raw_log
                WHERE username=? AND computer=? AND date=?
                ORDER BY time
            """, (username, computer, today)).fetchall()

            # Today's app events
            app_rows = conn.execute("""
                SELECT * FROM app_log
                WHERE username=? AND computer=? AND date=?
            """, (username, computer, today)).fetchall()

            # Monthly app events
            month_app = conn.execute("""
                SELECT * FROM app_log
                WHERE username=? AND computer=? AND date LIKE ?
            """, (username, computer, f"{this_month}%")).fetchall()

            # Monthly raw events
            month_raw = conn.execute("""
                SELECT * FROM raw_log
                WHERE username=? AND computer=? AND date LIKE ?
                ORDER BY date, time
            """, (username, computer, f"{this_month}%")).fetchall()

            # Today's top browser sites (aggregate by domain)
            browser_rows = conn.execute("""
                SELECT domain, MAX(secs) as secs FROM browser_log
                WHERE username=? AND computer=? AND date=?
                GROUP BY domain ORDER BY secs DESC LIMIT 10
            """, (username, computer, today)).fetchall()
            top_sites = [{"domain": r["domain"], "secs": r["secs"]} for r in browser_rows]

            # Latest VPN status
            vpn_row = conn.execute("""
                SELECT * FROM vpn_log WHERE username=? AND computer=?
                ORDER BY date DESC, time DESC LIMIT 1
            """, (username, computer)).fetchone()
            vpn_on       = bool(vpn_row and vpn_row["vpn_on"]) if vpn_row else False
            vpn_software = vpn_row["software"] if vpn_row else ""

            # Compute today stats
            first_login = "--"
            last_event_tm = "--"
            last_shutdown_tm = "--"
            last_event_dt = None
            serial = "N/A"
            location = "N/A"
            ip_addr = "N/A"

            for r in today_rows:
                ev = r["event"].upper()
                if "LOGIN" in ev and "LOGOUT" not in ev and first_login == "--":
                    first_login = r["time"]
                last_event_tm = r["time"]
                if "LOGOUT" in ev:
                    last_shutdown_tm = r["time"]
                if r["serial"] and r["serial"] not in ("N/A", ""):
                    serial = r["serial"]
                city = r["city"] or "N/A"
                reg  = r["region"] or ""
                if city and city != "N/A":
                    location = f"{city}, {reg}".strip(", ")
                ip = r["ip"] or "N/A"
                if ip and "." in ip and ip != "N/A":
                    ip_addr = ip
                try:
                    last_event_dt = datetime.strptime(
                        today + " " + r["time"], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            # Status â€” walk events newestâ†’oldest to find last meaningful state
            status = "Offline"
            if last_event_dt:
                mins_ago = (now_ist() - last_event_dt).total_seconds() / 60
                last_ev = today_rows[-1]["event"].upper() if today_rows else ""
                if last_ev in ("LOGOUT(SHUTDOWN)", "LOGOUT(LOGOFF)"):
                    status = "Offline"
                elif last_ev in ("LOGOUT(LOCK)", "LOGOUT(SCREEN-OFF)"):
                    status = "Offline"
                elif last_ev == "LOGOUT(IDLE)":
                    status = "Idle"
                elif "LOGIN" in last_ev:
                    if mins_ago > OFFLINE_MIN:
                        status = "Offline"
                    elif mins_ago > IDLE_MIN:
                        status = "Idle"
                    else:
                        status = "Online"
                elif last_ev == "HEARTBEAT":
                    if mins_ago > OFFLINE_MIN:
                        status = "Offline"
                    elif mins_ago > IDLE_MIN:
                        status = "Idle"
                    else:
                        status = "Online"
                else:
                    if mins_ago > OFFLINE_MIN:
                        status = "Offline"
                    elif mins_ago > IDLE_MIN:
                        status = "Idle"
                    else:
                        status = "Online"
            elif today_rows:
                status = "Offline"

            if status == "Online":   online_cnt += 1
            elif status == "Idle":   idle_cnt += 1
            else:                    offline_cnt += 1

            # Today app stats
            active_today_s = 0
            idle_today_s   = 0
            app_ctr = Counter()
            for ar in app_rows:
                dur_s = ar["duration_sec"] or 0
                state = (ar["state"] or "active").lower()
                apn   = ar["app"] or ""
                if state == "active":
                    active_today_s += dur_s
                    if apn:
                        app_ctr[apn] += dur_s
                else:
                    idle_today_s += dur_s
            top_app = friendly_name(app_ctr.most_common(1)[0][0]) if app_ctr else "N/A"
            top5_today = [{"app": friendly_name(a), "dur": fmt_secs(s)}
                          for a, s in app_ctr.most_common(5)]
            total_active_today += active_today_s

            # Monthly stats
            days_worked = set()
            month_active_s = 0
            for ar in month_app:
                if ar["state"].lower() == "active":
                    month_active_s += ar["duration_sec"] or 0
                    days_worked.add(ar["date"])

            # Monthly session hours (loginâ†’logout pairs)
            month_sess_s = 0
            pending_dt = None
            for r in month_raw:
                ev = r["event"].upper()
                try:
                    dt = datetime.strptime(r["date"] + " " + r["time"], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                if "LOGIN" in ev and "LOGOUT" not in ev:
                    pending_dt = dt
                elif "LOGOUT" in ev and pending_dt:
                    dur = (dt - pending_dt).total_seconds()
                    if 0 < dur < 86400:
                        month_sess_s += dur
                        days_worked.add(pending_dt.strftime("%Y-%m-%d"))
                    pending_dt = None

            rows.append({
                "username":        username,
                "computer":        computer,
                "serial":          serial,
                "status":          status,
                "first_login":     first_login,
                "last_shutdown":   last_shutdown_tm,
                "last_event":      last_event_tm,
                "location":        location,
                "ip":              ip_addr,
                "active_today":    fmt_secs(active_today_s),
                "idle_today":      fmt_secs(idle_today_s),
                "top_app":         top_app,
                "top5_today":      top5_today,
                "top_sites":       top_sites,
                "vpn_on":          vpn_on,
                "vpn_software":    vpn_software,
                "days_worked":     len(days_worked),
                "month_sess":      fmt_secs(month_sess_s),
                "month_active":    fmt_secs(month_active_s),
                "month_active_dec": fmt_dec(month_active_s),
                "act_s":           active_today_s,
            })

    status_order = {"Online": 0, "Idle": 1, "Offline": 2}
    rows.sort(key=lambda x: (status_order.get(x["status"], 3), x["username"]))
    return rows, online_cnt, idle_cnt, offline_cnt, total_active_today


def get_employee_detail(username, computer):
    this_month = now_ist().strftime("%Y-%m")
    today = now_ist().strftime("%Y-%m-%d")

    with get_db() as conn:
        today_raw = conn.execute("""
            SELECT * FROM raw_log WHERE username=? AND computer=? AND date=? ORDER BY time
        """, (username, computer, today)).fetchall()

        month_raw = conn.execute("""
            SELECT * FROM raw_log WHERE username=? AND computer=? AND date LIKE ? ORDER BY date,time
        """, (username, computer, f"{this_month}%")).fetchall()

        month_app = conn.execute("""
            SELECT * FROM app_log WHERE username=? AND computer=? AND date LIKE ?
        """, (username, computer, f"{this_month}%")).fetchall()

        today_app = conn.execute("""
            SELECT * FROM app_log WHERE username=? AND computer=? AND date=?
        """, (username, computer, today)).fetchall()

        recent_raw = conn.execute("""
            SELECT * FROM raw_log WHERE username=? AND computer=?
            ORDER BY date DESC, time DESC LIMIT 50
        """, (username, computer)).fetchall()

    # Today stats
    first_login = "--"
    last_event  = "--"
    last_shutdown = "--"
    last_event_dt = None
    serial = location = ip_addr = "N/A"

    for r in today_raw:
        ev = r["event"].upper()
        if "LOGIN" in ev and "LOGOUT" not in ev and first_login == "--":
            first_login = r["time"]
        last_event = r["time"]
        if "LOGOUT" in ev:
            last_shutdown = r["time"]
        if r["serial"] and r["serial"] not in ("N/A", ""):
            serial = r["serial"]
        city = r["city"] or "N/A"
        reg  = r["region"] or ""
        if city != "N/A":
            location = f"{city}, {reg}".strip(", ")
        ip = r["ip"] or "N/A"
        if ip and "." in ip and ip != "N/A":
            ip_addr = ip
        try:
            last_event_dt = datetime.strptime(today + " " + r["time"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    status = "Offline"
    if last_event_dt:
        mins_ago = (now_ist() - last_event_dt).total_seconds() / 60
        last_ev = today_raw[-1]["event"].upper() if today_raw else ""
        if last_ev in ("LOGOUT(SHUTDOWN)", "LOGOUT(LOGOFF)", "LOGOUT(LOCK)", "LOGOUT(SCREEN-OFF)"):
            status = "Offline"
        elif last_ev == "LOGOUT(IDLE)":
            status = "Idle"
        elif "LOGIN" in last_ev or last_ev == "HEARTBEAT":
            if mins_ago > OFFLINE_MIN:
                status = "Offline"
            elif mins_ago > IDLE_MIN:
                status = "Idle"
            else:
                status = "Online"
        else:
            if mins_ago > OFFLINE_MIN:
                status = "Offline"
            elif mins_ago > IDLE_MIN:
                status = "Idle"
            else:
                status = "Online"

    # Today app
    act_s = idle_s = 0
    app_ctr = Counter()
    for ar in today_app:
        dur_s = ar["duration_sec"] or 0
        state = (ar["state"] or "active").lower()
        apn   = ar["app"] or ""
        if state == "active":
            act_s += dur_s
            if apn:
                app_ctr[apn] += dur_s
        else:
            idle_s += dur_s
    top_app_today = app_ctr.most_common(1)[0][0] if app_ctr else "N/A"

    # Monthly calendar
    daily_act  = defaultdict(float)
    daily_sess = defaultdict(float)
    app_month  = Counter()

    pending_dt = None
    for r in month_raw:
        ev = r["event"].upper()
        try:
            dt = datetime.strptime(r["date"] + " " + r["time"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if "LOGIN" in ev and "LOGOUT" not in ev:
            pending_dt = dt
        elif "LOGOUT" in ev and pending_dt:
            dur = (dt - pending_dt).total_seconds()
            if 0 < dur < 86400:
                daily_sess[pending_dt.strftime("%Y-%m-%d")] += dur
            pending_dt = None

    for ar in month_app:
        dur_s = ar["duration_sec"] or 0
        if (ar["state"] or "active").lower() == "active":
            daily_act[ar["date"]] += dur_s
            apn = ar["app"] or ""
            if apn:
                app_month[apn] += dur_s

    now   = now_ist()
    first = now.replace(day=1)
    cal   = []
    for i in range(now.day):
        d   = (first + timedelta(days=i)).strftime("%Y-%m-%d")
        day = (first + timedelta(days=i))
        cal.append({
            "date":   d,
            "day":    day.strftime("%a %d"),
            "active": fmt_secs(daily_act.get(d, 0)),
            "sess":   fmt_secs(daily_sess.get(d, 0)),
            "dec":    fmt_dec(daily_act.get(d, 0)),
            "worked": daily_act.get(d, 0) > 0 or daily_sess.get(d, 0) > 0,
        })

    top10_apps = [{"app": a, "dur": fmt_secs(s)} for a, s in app_month.most_common(10)]
    days_worked = len(set(list(daily_act.keys()) + list(daily_sess.keys())))
    month_sess_s   = sum(daily_sess.values())
    month_active_s = sum(daily_act.values())

    recent = []
    for r in recent_raw:
        recent.append({
            "date": r["date"], "time": r["time"], "event": r["event"],
            "serial": r["serial"] or "N/A", "ip": r["ip"] or "N/A",
            "city": r["city"] or "N/A",
        })

    return {
        "username": username, "computer": computer,
        "serial": serial, "status": status,
        "location": location, "ip": ip_addr,
        "first_login": first_login, "last_shutdown": last_shutdown, "last_event": last_event,
        "active_today": fmt_secs(act_s),
        "idle_today":   fmt_secs(idle_s),
        "top_app_today": top_app_today,
        "days_worked":  days_worked,
        "month_sess":   fmt_secs(month_sess_s),
        "month_active": fmt_secs(month_active_s),
        "month_active_dec": fmt_dec(month_active_s),
        "cal":          cal,
        "top10_apps":   top10_apps,
        "recent":       recent,
    }


# â”€â”€ HTML TEMPLATES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
BASE_STYLE = """
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
<style>
  :root {
    --bg:        #070b14;
    --bg-grad1:  #0b1224;
    --bg-grad2:  #070b14;
    --surface:   rgba(22,30,48,.65);
    --surface2:  rgba(13,20,36,.8);
    --border:    rgba(99,141,200,.18);
    --border-hi: rgba(139,91,255,.4);
    --accent:    #6c8cff;
    --accent2:   #a855f7;
    --text:      #e7ecf5;
    --text-dim:  #7e93b5;
    --green:     #2dd4a7;
    --yellow:    #f5b942;
    --red:       #ef5a6f;
  }
  * { box-sizing: border-box; }
  body {
    background: radial-gradient(1300px 800px at 8% -15%, #1c2a66 0%, transparent 55%),
                radial-gradient(1100px 700px at 100% -5%, #4a1f8f 0%, transparent 50%),
                radial-gradient(900px 600px at 50% 110%, #1a0f3a 0%, transparent 60%),
                linear-gradient(180deg, var(--bg-grad1), var(--bg-grad2) 60%);
    color: var(--text);
    font-family: 'Inter', 'Segoe UI', sans-serif;
    min-height: 100vh;
    letter-spacing: -.005em;
  }
  .brand-logo {
    font-size: 1.18rem; font-weight: 800; letter-spacing: -.01em;
    background: linear-gradient(135deg, #8bb0ff 0%, #a855f7 60%, #c084fc 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .navbar-dark-custom {
    background: rgba(7,11,20,.7);
    backdrop-filter: blur(14px) saturate(140%);
    border-bottom: 1px solid var(--border);
    box-shadow: 0 1px 0 rgba(255,255,255,.03) inset;
  }
  .card-dark {
    background: var(--surface);
    backdrop-filter: blur(16px) saturate(150%);
    border: 1px solid var(--border);
    border-radius: 18px;
    box-shadow: 0 10px 40px rgba(0,0,0,.4), 0 0 0 1px rgba(168,85,247,.04), 0 1px 0 rgba(255,255,255,.05) inset;
  }
  .stat-card {
    background: linear-gradient(155deg, rgba(108,140,255,.18), rgba(168,85,247,.1) 55%, rgba(13,20,36,.45));
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 20px 24px;
    backdrop-filter: blur(12px);
    transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
    position: relative;
    overflow: hidden;
  }
  .stat-card::before {
    content: ''; position: absolute; top: -40%; right: -30%; width: 120px; height: 120px;
    background: radial-gradient(circle, rgba(168,85,247,.25), transparent 70%);
    border-radius: 50%; pointer-events: none;
  }
  .stat-card:hover {
    transform: translateY(-3px);
    border-color: var(--border-hi);
    box-shadow: 0 12px 30px rgba(108,80,255,.15);
  }
  .stat-num { font-size: 2.35rem; font-weight: 800; letter-spacing: -.03em; }
  .badge-online, .badge-idle, .badge-offline {
    padding: 4px 12px; border-radius: 20px; font-size: .76rem; font-weight: 600;
    letter-spacing: .02em;
  }
  .badge-online  { background: rgba(45,212,167,.15); color: var(--green);  border: 1px solid rgba(45,212,167,.35); }
  .badge-idle    { background: rgba(245,185,66,.15); color: var(--yellow); border: 1px solid rgba(245,185,66,.35); }
  .badge-offline { background: rgba(239,90,111,.15); color: var(--red);    border: 1px solid rgba(239,90,111,.35); }
  .table-dark-custom { background: transparent; color: var(--text); }
  .table-dark-custom th {
    background: rgba(10,16,30,.7); color: var(--accent); border-color: var(--border);
    font-size: .74rem; text-transform: uppercase; letter-spacing: .07em; font-weight: 700;
    padding: 12px 14px;
  }
  .table-dark-custom td { border-color: rgba(99,141,200,.1); font-size: .87rem; vertical-align: middle; padding: 11px 14px; }
  .table-dark-custom tr { transition: background .12s ease; }
  .table-dark-custom tr:hover td { background: rgba(91,140,255,.06); }
  .dot-online, .dot-idle, .dot-offline {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 7px;
  }
  .dot-online  { background: var(--green);  box-shadow: 0 0 8px var(--green);  animation: pulse 2s infinite; }
  .dot-idle    { background: var(--yellow); box-shadow: 0 0 6px var(--yellow); }
  .dot-offline { background: var(--red); }
  @keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:.35;} }
  .btn-detail {
    background: linear-gradient(135deg, rgba(91,140,255,.25), rgba(139,91,255,.18));
    border: 1px solid var(--border-hi); color: #b9cdff; font-size: .78rem; font-weight: 600;
    padding: 5px 14px; border-radius: 8px; transition: all .15s ease;
  }
  .btn-detail:hover { background: linear-gradient(135deg,var(--accent),var(--accent2)); color: #fff; border-color: transparent; }
  .section-title { color: var(--accent); font-size: .74rem; text-transform: uppercase; letter-spacing: .1em; margin-bottom: 8px; font-weight: 700; }
  .refresh-note { font-size: .72rem; color: var(--text-dim); }
  .cal-day {
    background: rgba(13,20,36,.6); border: 1px solid var(--border); border-radius: 10px;
    padding: 8px 10px; margin: 3px; min-width: 80px; display: inline-block; text-align: center; font-size: .78rem;
  }
  .cal-day.worked { border-color: rgba(45,212,167,.4); background: rgba(45,212,167,.06); }
  .cal-day .hrs { font-size: 1rem; font-weight: 700; color: var(--green); }
  input.form-control { background: rgba(13,20,36,.7) !important; border-color: var(--border) !important; color: var(--text) !important; border-radius: 9px !important; }
  input.form-control:focus { border-color: var(--accent) !important; box-shadow: 0 0 0 3px rgba(91,140,255,.15) !important; }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(91,140,255,.25); border-radius: 10px; }
  ::-webkit-scrollbar-thumb:hover { background: rgba(91,140,255,.45); }

  /* â”€â”€ LIGHT THEME â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
  html[data-theme="light"] {
    --border: rgba(60,90,160,.16); --border-hi: rgba(60,90,160,.32);
    --text: #1a2030; --text-dim: #5b6a85;
  }
  html[data-theme="light"] body {
    background: radial-gradient(1200px 700px at 10% -10%, #e8edff 0%, transparent 60%),
                radial-gradient(1000px 600px at 100% 0%, #f1e8ff 0%, transparent 55%),
                linear-gradient(180deg, #f7f9fd, #eef1f8 60%);
    color: var(--text);
  }
  html[data-theme="light"] .navbar-dark-custom,
  html[data-theme="light"] .topbar {
    background: rgba(255,255,255,.75); box-shadow: 0 1px 0 rgba(0,0,0,.03) inset;
  }
  html[data-theme="light"] .card-dark,
  html[data-theme="light"] .emp-card,
  html[data-theme="light"] .table-wrap {
    background: rgba(255,255,255,.7); box-shadow: 0 8px 24px rgba(30,50,100,.08), 0 1px 0 rgba(255,255,255,.6) inset;
  }
  html[data-theme="light"] .stat-card,
  html[data-theme="light"] .stat-box {
    background: linear-gradient(155deg, rgba(91,140,255,.10), rgba(139,91,255,.05) 60%, rgba(255,255,255,.5));
  }
  html[data-theme="light"] .table-dark-custom th { background: rgba(240,244,255,.8); }
  html[data-theme="light"] .table-dark-custom tr:hover td,
  html[data-theme="light"] tbody tr:hover td { background: rgba(91,140,255,.07); }
  html[data-theme="light"] input.form-control {
    background: rgba(255,255,255,.85) !important; color: var(--text) !important;
  }
  html[data-theme="light"] .month-btn { background: rgba(91,140,255,.08); color: #345; }
  html[data-theme="light"] .app-chip { background: rgba(91,140,255,.07); color: #2a4a8a; }
  html[data-theme="light"] .alert-row { background: rgba(240,244,255,.6); }
  html[data-theme="light"] .no-alert { color: #4a8a6a; }
  html[data-theme="light"] .emp-header { background: linear-gradient(90deg, rgba(91,140,255,.08), rgba(139,91,255,.03)); }
  html[data-theme="light"] .cal-day { background: rgba(240,244,255,.7); }

  .theme-toggle {
    background: rgba(91,140,255,.1); border: 1px solid var(--border); color: var(--accent);
    width: 34px; height: 34px; border-radius: 50%; cursor: pointer; font-size: .95rem;
    display: inline-flex; align-items: center; justify-content: center; transition: all .15s ease;
  }
  .theme-toggle:hover { background: linear-gradient(135deg,var(--accent),var(--accent2)); color: #fff; }
</style>
<script>
  (function() {
    const saved = localStorage.getItem('empmon_theme') || 'light';
    document.documentElement.setAttribute('data-theme', saved);
  })();
  function toggleTheme() {
    const cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', cur);
    localStorage.setItem('empmon_theme', cur);
    const btn = document.getElementById('themeToggleBtn');
    if (btn) btn.innerHTML = cur === 'light' ? '<i class="fa fa-moon"></i>' : '<i class="fa fa-sun"></i>';
  }
  document.addEventListener('DOMContentLoaded', function() {
    const btn = document.getElementById('themeToggleBtn');
    if (btn) {
      const cur = document.documentElement.getAttribute('data-theme');
      btn.innerHTML = cur === 'light' ? '<i class="fa fa-moon"></i>' : '<i class="fa fa-sun"></i>';
    }
  });
</script>
"""

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>""" + BASE_STYLE + """
<meta http-equiv="refresh" content="{{ refresh }}">
<title>{{ company }} | Employee Dashboard</title>
</head>
<body>
<nav class="navbar-dark-custom px-4 py-3 d-flex justify-content-between align-items-center">
  <div>
    <span class="brand-logo">
      <i class="fa fa-shield-halved me-2" style="color:#6c8cff;"></i>{{ company }}
    </span>
    <span class="ms-3" style="color:#4a7a9b;font-size:.82rem;">Employee Monitor Dashboard v8</span>
  </div>
  <div class="d-flex align-items-center gap-3">
    <span class="refresh-note"><i class="fa fa-rotate me-1"></i>Auto-refresh {{ refresh }}s</span>
    <span style="color:#4a7a9b;font-size:.82rem;">{{ now }}</span>
    <button id="themeToggleBtn" class="theme-toggle" onclick="toggleTheme()" title="Toggle light/dark theme"></button>
  </div>
</nav>

<div class="container-fluid px-4 py-4">
  <div class="row g-3 mb-4">
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="section-title"><i class="fa fa-users me-1"></i>Total Employees</div>
        <div class="stat-num text-info">{{ total }}</div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="section-title"><i class="fa fa-circle me-1" style="color:#22c55e"></i>Online Now</div>
        <div class="stat-num" style="color:#22c55e;">{{ online }}</div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="section-title"><i class="fa fa-circle me-1" style="color:#eab308"></i>Idle</div>
        <div class="stat-num" style="color:#eab308;">{{ idle }}</div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="section-title"><i class="fa fa-clock me-1" style="color:#60a5fa"></i>Total Active Today</div>
        <div class="stat-num" style="color:#60a5fa;font-size:1.5rem;">{{ total_active }}</div>
      </div>
    </div>
  </div>

  <div class="card-dark p-3">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <span style="color:#7ab3e0;font-weight:600;"><i class="fa fa-table me-2"></i>All Employees â€” Today ({{ today }})</span>
      <input type="text" id="srch" class="form-control form-control-sm w-auto"
             placeholder="Search..." onkeyup="filterTable()"
             style="background:#0d1e30;border-color:#1e3a5f;color:#e0e6ed;min-width:180px;">
    </div>
    <div class="table-responsive">
    <table class="table table-dark-custom table-hover mb-0" id="empTable">
      <thead><tr>
        <th>#</th><th>Username</th><th>Computer</th><th>Serial</th>
        <th>Status</th><th>VPN</th><th>First Login</th><th>Shutdown/Logout</th><th>Last Activity</th>
        <th>Active Hrs Today</th><th>Idle Hrs</th><th>Top App</th>
        <th>Location</th><th>IP</th>
        <th>Days/Month</th><th>Month Active</th><th>Detail</th>
      </tr></thead>
      <tbody>
      {% for i, e in employees %}
      <tr>
        <td class="text-muted">{{ i }}</td>
        <td><strong style="color:#7ab3e0;">{{ e.username }}</strong></td>
        <td>{{ e.computer }}</td>
        <td><small class="text-muted">{{ e.serial }}</small></td>
        <td>
          {% if e.status == 'Online' %}<span class="dot-online"></span><span class="badge-online">Online</span>
          {% elif e.status == 'Idle' %}<span class="dot-idle"></span><span class="badge-idle">Idle</span>
          {% else %}<span class="dot-offline"></span><span class="badge-offline">Offline</span>{% endif %}
        </td>
        <td>
          {% if e.vpn_on and e.vpn_software and e.vpn_software.startswith('REMOTE:') %}
            <span style="color:#ef4444;font-weight:700;animation:blink 1s infinite;">&#128187; {{ e.vpn_software[7:] }}</span>
          {% elif e.vpn_on and e.vpn_software %}
            <span style="color:#f97316;font-weight:600;">&#128274; {{ e.vpn_software }}</span>
          {% elif e.vpn_on %}
            <span style="color:#f97316;font-weight:600;">&#128274; VPN Active</span>
          {% else %}
            <span style="color:#475569;font-size:.75rem;">â€”</span>
          {% endif %}
        </td>
        <td>{{ e.first_login }}</td>
        <td><small style="color:#ef4444;">{{ e.last_shutdown }}</small></td>
        <td><small>{{ e.last_event }}</small></td>
        <td><strong style="color:#22c55e;">{{ e.active_today }}</strong></td>
        <td><small class="text-muted">{{ e.idle_today }}</small></td>
        <td>
          {% for a in e.top5_today %}
          <div style="font-size:.75rem;white-space:nowrap;">
            <span style="color:#3a6a9a;">{{ loop.index }}.</span>
            <span style="color:{% if 'SAP' in a.app %}#f97316{% elif 'Chrome' in a.app or 'Edge' in a.app or 'Firefox' in a.app %}#60a5fa{% elif 'Teams' in a.app or 'Outlook' in a.app or 'Zoom' in a.app %}#a78bfa{% elif 'Excel' in a.app or 'Word' in a.app %}#22c55e{% else %}#cbd5e1{% endif %};">{{ a.app }}</span>
            <span style="color:#475569;margin-left:4px;">{{ a.dur }}</span>
          </div>
          {% endfor %}
          {% if not e.top5_today %}<small class="text-muted">No data</small>{% endif %}
        </td>
        <td><small class="text-muted"><i class="fa fa-location-dot me-1" style="color:#3b82f6;"></i>{{ e.location }}</small></td>
        <td><small class="text-muted">{{ e.ip }}</small></td>
        <td>{{ e.days_worked }} days<br><small class="text-muted">{{ e.month_sess }}</small></td>
        <td><strong style="color:#60a5fa;">{{ e.month_active }}</strong><br>
            <small class="text-muted">{{ e.month_active_dec }} hrs</small></td>
        <td><a href="/employee/{{ e.username }}/{{ e.computer }}" class="btn btn-detail">
            <i class="fa fa-eye me-1"></i>View</a></td>
      </tr>
      {% endfor %}
      {% if not employees %}
      <tr><td colspan="15" class="text-center text-muted py-5">
        <i class="fa fa-database fa-2x mb-2 d-block"></i>
        No employee data yet.<br>
        <small>Deploy <strong>employee_agent.py</strong> on each employee PC and point it to this server.</small>
      </td></tr>
      {% endif %}
      </tbody>
    </table>
    </div>
  </div>
  <div class="mt-3 text-muted" style="font-size:.75rem;">
    <i class="fa fa-database me-1"></i>SQLite DB: <code style="color:#4a7a9b;">{{ db_path }}</code>
    &nbsp;|&nbsp; Online = activity within {{ idle_min }}min &nbsp;|&nbsp;
    Idle = {{ idle_min }}-{{ offline_min }}min &nbsp;|&nbsp; Offline = &gt;{{ offline_min }}min
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<script>
function filterTable() {
  const q = document.getElementById("srch").value.toLowerCase();
  document.querySelectorAll("#empTable tbody tr").forEach(r => {
    r.style.display = r.innerText.toLowerCase().includes(q) ? "" : "none";
  });
}
</script>
</body></html>"""

DETAIL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>""" + BASE_STYLE + """
<meta http-equiv="refresh" content="{{ refresh }}">
<title>{{ e.username }} | Employee Detail</title>
</head>
<body>
<nav class="navbar-dark-custom px-4 py-3 d-flex justify-content-between align-items-center">
  <div>
    <span class="brand-logo">
      <i class="fa fa-shield-halved me-2" style="color:#6c8cff;"></i>{{ company }}
    </span>
  </div>
  <span style="color:#4a7a9b;font-size:.82rem;">{{ now }}</span>
</nav>

<div class="container-fluid px-4 py-4">
  <div class="mb-3">
    <a href="/" style="color:#7ab3e0;text-decoration:none;"><i class="fa fa-arrow-left me-2"></i>Back to Dashboard</a>
  </div>

  <div class="card-dark p-4 mb-4">
    <div class="row align-items-center">
      <div class="col-md-6">
        <h4 class="mb-1" style="color:#7ab3e0;"><i class="fa fa-user-circle me-2"></i>{{ e.username }}</h4>
        <div class="text-muted" style="font-size:.88rem;">
          <i class="fa fa-desktop me-1"></i>{{ e.computer }}
          &nbsp;|&nbsp; <i class="fa fa-barcode me-1"></i>Serial: <strong style="color:#e0e6ed;">{{ e.serial }}</strong>
          &nbsp;|&nbsp; <i class="fa fa-location-dot me-1"></i>{{ e.location }}
          &nbsp;|&nbsp; IP: {{ e.ip }}
        </div>
      </div>
      <div class="col-md-6 text-md-end mt-3 mt-md-0">
        {% if e.status == 'Online' %}
          <span class="badge-online" style="font-size:.95rem;padding:6px 16px;">
            <i class="fa fa-circle me-1" style="color:#22c55e;"></i>Online Now
          </span>
        {% elif e.status == 'Idle' %}
          <span class="badge-idle" style="font-size:.95rem;padding:6px 16px;">
            <i class="fa fa-circle me-1" style="color:#eab308;"></i>Idle
          </span>
        {% else %}
          <span class="badge-offline" style="font-size:.95rem;padding:6px 16px;">
            <i class="fa fa-circle me-1" style="color:#ef4444;"></i>Offline
          </span>
        {% endif %}
      </div>
    </div>
  </div>

  <div class="row g-3 mb-4">
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="section-title">First Login Today</div>
        <div style="font-size:1.4rem;font-weight:700;color:#60a5fa;">{{ e.first_login }}</div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="section-title">Shutdown / Logout</div>
        <div style="font-size:1.4rem;font-weight:700;color:#ef4444;">{{ e.last_shutdown }}</div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="section-title">Active Hrs Today</div>
        <div style="font-size:1.4rem;font-weight:700;color:#22c55e;">{{ e.active_today }}</div>
        <small class="text-muted">Idle: {{ e.idle_today }}</small>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="section-title">This Month</div>
        <div style="font-size:1.4rem;font-weight:700;color:#a78bfa;">{{ e.month_active }}</div>
        <small class="text-muted">{{ e.month_active_dec }} hrs &nbsp;|&nbsp; {{ e.days_worked }} days</small>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="stat-card">
        <div class="section-title">Top App Today</div>
        <div style="font-size:1rem;font-weight:700;color:#fb923c;">{{ e.top_app_today }}</div>
        <small class="text-muted">Session hrs: {{ e.month_sess }}</small>
      </div>
    </div>
  </div>

  <div class="row g-3">
    <div class="col-lg-8">
      <div class="card-dark p-3 mb-3">
        <div class="section-title mb-3"><i class="fa fa-calendar me-1"></i>This Month â€” Daily Active Hours</div>
        <div>
        {% for d in e.cal %}
          <div class="cal-day {% if d.worked %}worked{% endif %}">
            <div style="color:#4a7a9b;font-size:.7rem;">{{ d.day }}</div>
            {% if d.worked %}
              <div class="hrs">{{ d.dec }}</div>
              <div style="color:#4a9b6a;font-size:.68rem;">{{ d.active }}</div>
            {% else %}
              <div style="color:#3a4a5a;font-size:.85rem;">â€”</div>
            {% endif %}
          </div>
        {% endfor %}
        </div>
      </div>
      <div class="card-dark p-3">
        <div class="section-title mb-2"><i class="fa fa-list me-1"></i>Recent Events (last 50)</div>
        <div class="table-responsive" style="max-height:320px;overflow-y:auto;">
        <table class="table table-dark-custom mb-0" style="font-size:.8rem;">
          <thead><tr><th>Date</th><th>Time</th><th>Event</th><th>Serial</th><th>IP</th><th>City</th></tr></thead>
          <tbody>
          {% for r in e.recent %}
          <tr>
            <td>{{ r.date }}</td><td>{{ r.time }}</td>
            <td>
              {% set ev = r.event | upper %}
              {% if ev == 'LOGIN' %}
                <span style="color:#22c55e;font-weight:600;">&#9654; LOGIN</span>
              {% elif ev == 'LOGIN(UNLOCK)' %}
                <span style="color:#22c55e;">&#128275; Unlock</span>
              {% elif ev == 'LOGIN(SCREEN-ON)' %}
                <span style="color:#22c55e;">&#128161; Screen ON</span>
              {% elif ev == 'LOGIN(IDLE-RESUME)' %}
                <span style="color:#86efac;">&#9654; Idle Resume</span>
              {% elif ev == 'LOGOUT(SHUTDOWN)' %}
                <span style="color:#ef4444;font-weight:600;">&#9209; SHUTDOWN</span>
              {% elif ev == 'LOGOUT(LOGOFF)' %}
                <span style="color:#ef4444;font-weight:600;">&#128682; Log Off</span>
              {% elif ev == 'LOGOUT(LOCK)' %}
                <span style="color:#f97316;">&#128274; Locked</span>
              {% elif ev == 'LOGOUT(SCREEN-OFF)' %}
                <span style="color:#f97316;">&#127769; Screen OFF</span>
              {% elif ev == 'LOGOUT(IDLE)' %}
                <span style="color:#fbbf24;">&#128336; Idle</span>
              {% elif ev == 'HEARTBEAT' %}
                <span style="color:#475569;">&#9679; Heartbeat</span>
              {% elif 'LOGIN' in ev %}
                <span style="color:#22c55e;">&#9654; {{ r.event }}</span>
              {% elif 'LOGOUT' in ev %}
                <span style="color:#ef4444;">&#9209; {{ r.event }}</span>
              {% else %}
                <span style="color:#94a3b8;">{{ r.event }}</span>
              {% endif %}
            </td>
            <td><small class="text-muted">{{ r.serial }}</small></td>
            <td><small class="text-muted">{{ r.ip }}</small></td>
            <td><small class="text-muted">{{ r.city }}</small></td>
          </tr>
          {% endfor %}
          </tbody>
        </table>
        </div>
      </div>
    </div>
    <div class="col-lg-4">
      <div class="card-dark p-3">
        <div class="section-title mb-3"><i class="fa fa-chart-bar me-1"></i>Top 10 Apps This Month</div>
        {% for a in e.top10_apps %}
        <div class="d-flex justify-content-between align-items-center mb-2">
          <div style="font-size:.83rem;color:#c0cfe0;max-width:65%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
            <span style="color:#4a7a9b;margin-right:6px;">{{ loop.index }}</span>{{ a.app }}
          </div>
          <div style="font-size:.83rem;color:#60a5fa;font-weight:600;">{{ a.dur }}</div>
        </div>
        {% endfor %}
        {% if not e.top10_apps %}
        <div class="text-muted" style="font-size:.83rem;">No app data for this month.</div>
        {% endif %}
      </div>
    </div>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
</body></html>"""


# â”€â”€ ROUTES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.route("/")
def index():
    try:
        rows, online, idle_cnt, offline, total_active = get_all_employees_today()
        today   = now_ist().strftime("%Y-%m-%d")
        now_str = now_ist().strftime("%Y-%m-%d %H:%M:%S")
        return render_template_string(
            INDEX_HTML,
            company=COMPANY, refresh=REFRESH_S, now=now_str, today=today,
            total=len(rows), online=online, idle=idle_cnt,
            employees=list(enumerate(rows, 1)),
            total_active=fmt_secs(total_active),
            db_path=DB_PATH, idle_min=IDLE_MIN, offline_min=OFFLINE_MIN,
        )
    except Exception as e:
        import traceback
        return f"<pre>ERROR: {traceback.format_exc()}</pre>", 500


@app.route("/employee/<username>/<computer>")
def employee_detail(username, computer):
    e       = get_employee_detail(username, computer)
    now_str = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    return render_template_string(
        DETAIL_HTML, company=COMPANY, refresh=REFRESH_S, now=now_str, e=e)


@app.route("/api/summary")
def api_summary():
    rows, online, idle_cnt, offline, total_active = get_all_employees_today()
    return jsonify({
        "generated": now_ist().isoformat(),
        "total": len(rows), "online": online,
        "idle": idle_cnt, "offline": offline,
        "total_active_today_hrs": fmt_dec(total_active),
        "employees": rows,
    })


@app.route("/api/status")
def api_status():
    return jsonify({"status": "ok", "server": COMPANY, "version": "8.0"})


@app.route("/api/clear_all", methods=["POST"])
def clear_all():
    with get_db() as conn:
        conn.execute("DELETE FROM raw_log")
        conn.execute("DELETE FROM app_log")
        conn.execute("DELETE FROM disk_log")
        conn.execute("DELETE FROM vpn_log")
        conn.execute("DELETE FROM usb_log")
    return jsonify({"status": "ok", "message": "All data cleared"})


# â”€â”€ KEYWORD MAPS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
SOCIAL_MAP = {
    "youtube":"YouTube","instagram":"Instagram","facebook":"Facebook",
    "whatsapp":"WhatsApp","twitter":"Twitter/X","x.com":"Twitter/X",
    "tiktok":"TikTok","snapchat":"Snapchat","linkedin":"LinkedIn",
    "reddit":"Reddit","telegram":"Telegram","netflix":"Netflix",
    "hotstar":"Hotstar","spotify":"Spotify","discord":"Discord",
    "threads":"Threads","gmail":"Gmail","mail.google":"Gmail",
}
FILE_SHARE_MAP = {
    "whatsapp":"WhatsApp","telegram":"Telegram","gmail":"Gmail",
    "mail.google":"Gmail","onedrive":"OneDrive","sharepoint":"SharePoint",
    "dropbox":"Dropbox","google drive":"Google Drive","drive.google":"Google Drive",
    "wetransfer":"WeTransfer","filezilla":"FileZilla","winscp":"WinSCP",
    "box.com":"Box","mega.nz":"Mega","anydesk":"AnyDesk","teamviewer":"TeamViewer",
}
# External / personal email providers â€” using these for company work is a data-leak risk
EXTERNAL_EMAIL_MAP = {
    "mail.google.com":"Gmail", "gmail.com":"Gmail",
    "outlook.live.com":"Outlook.com (Personal)", "hotmail.com":"Hotmail",
    "mail.yahoo.com":"Yahoo Mail", "yahoo.com":"Yahoo Mail",
    "mail.rediffmail.com":"Rediffmail", "rediffmail.com":"Rediffmail",
    "protonmail.com":"ProtonMail", "zoho.com/mail":"Zoho Mail (Personal)",
    "icloud.com":"iCloud Mail", "aol.com":"AOL Mail",
}
WORK_KEYS  = ["excel","winword","powerpnt","onenote","acrobat","adobe","foxit",
              "notepad","mstsc","putty","sap","saplogon","sapgui","tally","tallyprime",
              "code","pycharm","studio","explorer","onedrive","sharepoint",
              "oracle","navision","dynamics","quickbooks","sage","msaccess",
              "winrar","7zip","filezilla","winscp","anydesk","teamviewer",
              "notepad++","sublime","vim","putty","cmd","powershell","taskmgr"]
COMMS_KEYS = ["outlook","teams","ms-teams","zoom","slack","skype","webex",
              "thunderbird","whatsapp","telegram","meetgeek","lync","gmail",
              "mattermost","discord","googlemeeting","meet","ringcentral"]

# Friendly display names for common .exe apps
APP_NAMES = {
    "msedge.exe":       "Microsoft Edge",
    "chrome.exe":       "Google Chrome",
    "firefox.exe":      "Mozilla Firefox",
    "OUTLOOK.EXE":      "Microsoft Outlook",
    "outlook.exe":      "Microsoft Outlook",
    "ms-teams.exe":     "Microsoft Teams",
    "Teams.exe":        "Microsoft Teams",
    "EXCEL.EXE":        "Microsoft Excel",
    "excel.exe":        "Microsoft Excel",
    "WINWORD.EXE":      "Microsoft Word",
    "winword.exe":      "Microsoft Word",
    "POWERPNT.EXE":     "PowerPoint",
    "powerpnt.exe":     "PowerPoint",
    "saplogon.exe":     "SAP Logon",
    "sapgui.exe":       "SAP GUI",
    "SAPgui.exe":       "SAP GUI",
    "sap.exe":          "SAP",
    "SAPGUI.EXE":       "SAP GUI",
    "zoom.exe":         "Zoom",
    "slack.exe":        "Slack",
    "whatsapp.exe":     "WhatsApp",
    "telegram.exe":     "Telegram",
    "discord.exe":      "Discord",
    "skype.exe":        "Skype",
    "Code.exe":         "VS Code",
    "code.exe":         "VS Code",
    "notepad.exe":      "Notepad",
    "notepad++.exe":    "Notepad++",
    "ONENOTE.EXE":      "OneNote",
    "onenote.exe":      "OneNote",
    "acrobat.exe":      "Adobe Acrobat",
    "AcroRd32.exe":     "Adobe Reader",
    "mstsc.exe":        "Remote Desktop",
    "explorer.exe":     "Windows Explorer",
    "taskmgr.exe":      "Task Manager",
    "powershell.exe":   "PowerShell",
    "cmd.exe":          "Command Prompt",
    "claude.exe":       "Claude AI",
    "tally.exe":        "Tally",
    "tallyprime.exe":   "TallyPrime",
    "anydesk.exe":      "AnyDesk",
    "teamviewer.exe":   "TeamViewer",
}


def friendly_name(app):
    return APP_NAMES.get(app, APP_NAMES.get(app.lower(), app))


def classify(app, title):
    al, tl = app.lower(), title.lower()
    for k in COMMS_KEYS:
        if k in al: return "comms"
    # Title-based comms detection (browser Gmail/Teams)
    if any(b in al for b in ("chrome","firefox","msedge","edge","opera","brave")):
        if any(k in tl for k in ("gmail","google meet","teams","zoom","outlook","webex","skype")):
            return "comms"
        for k in SOCIAL_MAP:
            if k in tl: return "nonwork"
        return "work"
    for k in WORK_KEYS:
        if k in al: return "work"
    return "work"


# â”€â”€ MONTHLY SUMMARY DATA BUILDER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def build_monthly_summary(month_str):
    """Build full monthly summary for all employees for a given month (YYYY-MM)."""
    with get_db() as conn:
        emps = conn.execute(
            "SELECT DISTINCT username, computer FROM raw_log ORDER BY username"
        ).fetchall()

    results = []
    for emp in emps:
        username = emp["username"]
        computer = emp["computer"]

        with get_db() as conn:
            raw = conn.execute("""
                SELECT * FROM raw_log
                WHERE username=? AND computer=? AND date LIKE ?
                ORDER BY date, time
            """, (username, computer, f"{month_str}%")).fetchall()

            app_rows = conn.execute("""
                SELECT * FROM app_log
                WHERE username=? AND computer=? AND date LIKE ?
            """, (username, computer, f"{month_str}%")).fetchall()

            disk_rows = conn.execute("""
                SELECT * FROM disk_log
                WHERE username=? AND computer=?
                ORDER BY date DESC, time DESC LIMIT 20
            """, (username, computer)).fetchall()

            vpn_rows = conn.execute("""
                SELECT * FROM vpn_log WHERE username=? AND computer=? AND date LIKE ? AND vpn_on=1
                ORDER BY date DESC, time DESC LIMIT 5
            """, (username, computer, f"{month_str}%")).fetchall()

            usb_rows = conn.execute("""
                SELECT * FROM usb_log WHERE username=? AND computer=? AND date LIKE ?
                ORDER BY date DESC, time DESC LIMIT 20
            """, (username, computer, f"{month_str}%")).fetchall()

        if not raw and not app_rows:
            continue

        # USB device alerts (dedupe by drive+label)
        seen_usb = set()
        usb_alerts = []
        for u in usb_rows:
            key = (u["drive"], u["label"])
            if key not in seen_usb:
                seen_usb.add(key)
                usb_alerts.append({
                    "drive": u["drive"], "label": u["label"] or u["drive"],
                    "size_gb": u["size_gb"], "date": u["date"], "time": u["time"],
                })

        # Daily login / shutdown / lock / unlock breakdown
        daily_events = defaultdict(lambda: {"login": "--", "shutdown": "--", "lock": [], "unlock": []})
        for r in raw:
            ev = r["event"].upper()
            d  = r["date"]
            if ev.startswith("LOGIN") and daily_events[d]["login"] == "--":
                daily_events[d]["login"] = r["time"]
            elif ev in ("LOGOUT(SHUTDOWN)", "LOGOUT(LOGOFF)"):
                daily_events[d]["shutdown"] = r["time"]
            elif ev == "LOGOUT(LOCK)":
                daily_events[d]["lock"].append(r["time"])
            elif ev == "LOGIN(UNLOCK)":
                daily_events[d]["unlock"].append(r["time"])
        # Per-day active/work/comms secs from app_log
        day_active = defaultdict(int)
        day_work   = defaultdict(int)
        day_comms  = defaultdict(int)
        day_first  = {}
        day_last   = {}
        for ar in app_rows:
            if (ar["state"] or "active").lower() == "active":
                dur = ar["duration_sec"] or 0
                day_active[ar["date"]] += dur
                cat = classify(ar["app"] or "", ar["window_title"] or "")
                if cat == "work":  day_work[ar["date"]]  += dur
                elif cat == "comms": day_comms[ar["date"]] += dur
        for r in raw:
            d = r["date"]
            if d not in day_first: day_first[d] = r["time"]
            day_last[d] = r["time"]
            # Ensure date exists in daily_events even if only HEARTBEAT
            if d not in daily_events:
                daily_events[d] = {"login": "--", "shutdown": "--", "lock": [], "unlock": []}
        # Also create daily entries from app_log dates (for employees with no raw LOGIN)
        for ar in app_rows:
            d = ar["date"]
            if d not in daily_events:
                daily_events[d] = {"login": "--", "shutdown": "--", "lock": [], "unlock": []}
            # Use earliest app start as login if no LOGIN event recorded
            t = ar["start_time"] or ""
            if t and daily_events[d]["login"] == "--":
                daily_events[d]["login"] = t
            elif t and daily_events[d]["login"] > t:
                daily_events[d]["login"] = t

        daily_breakdown = []
        for d, v in sorted(daily_events.items()):
            active_s = day_active.get(d, 0)
            session_s = 0
            if d in day_first and d in day_last and day_first[d] != day_last[d]:
                try:
                    s  = datetime.strptime(d+" "+day_first[d], "%Y-%m-%d %H:%M:%S")
                    en = datetime.strptime(d+" "+day_last[d],  "%Y-%m-%d %H:%M:%S")
                    session_s = max(0, (en - s).total_seconds())
                except Exception:
                    pass
            idle_s = max(0, session_s - active_s)
            daily_breakdown.append({
                "date":        d,
                "login":       v["login"],
                "shutdown":    v["shutdown"],
                "lockCount":   len(v["lock"]),
                "unlockCount": len(v["unlock"]),
                "lockTimes":   ", ".join(v["lock"][:3]),
                "unlockTimes": ", ".join(v["unlock"][:3]),
                "workSecs":    day_work.get(d, 0),
                "commsSecs":   day_comms.get(d, 0),
                "activeSecs":  active_s,
                "idleSecs":    idle_s,
                "sessionSecs": session_s,
            })

        # External / personal email detection
        external_email = defaultdict(int)
        for ar in app_rows:
            al = (ar["app"] or "").lower()
            tl = (ar["window_title"] or "").lower()
            for kw, pname in EXTERNAL_EMAIL_MAP.items():
                if kw in al or kw in tl:
                    external_email[pname] += ar["duration_sec"] or 0
                    break
        external_email_alert = [
            {"provider": p, "dur": fmt_secs(s),
             "risk": "HIGH" if s >= 3600 else "MEDIUM" if s >= 600 else "LOW"}
            for p, s in sorted(external_email.items(), key=lambda x: -x[1])
        ]

        # Latest disk snapshot per drive
        seen_drives = set()
        disks = []
        for d in disk_rows:
            drv = d["drive"]
            if drv not in seen_drives:
                seen_drives.add(drv)
                pct = d["pct_used"] or 0
                disks.append({
                    "drive":    drv,
                    "total_gb": d["total_gb"],
                    "used_gb":  d["used_gb"],
                    "free_gb":  d["free_gb"],
                    "pct_used": pct,
                    "alert":    "HIGH" if pct >= 90 else "WARN" if pct >= 75 else "OK",
                })
        if not disks:
            disks = [{"drive": "C:", "total_gb": 0, "used_gb": 0, "free_gb": 0, "pct_used": 0, "alert": "OK"}]

        # Serial, location, IP â€” from most recent row
        serial = ip_addr = location = "N/A"
        for r in reversed(raw):
            if r["serial"] and r["serial"] not in ("N/A",""):
                serial = r["serial"]
            city = r["city"] or "N/A"
            reg  = r["region"] or ""
            if city and city != "N/A":
                location = f"{city}, {reg}".strip(", ")
            ip = r["ip"] or "N/A"
            if ip and "." in ip and ip != "N/A":
                ip_addr = ip
            if serial != "N/A" and location != "N/A" and ip_addr != "N/A":
                break

        # Days worked + session hours
        days_worked = set()
        pending_dt  = None
        sess_secs   = 0.0
        for r in raw:
            ev = r["event"].upper()
            try:
                dt = datetime.strptime(r["date"]+" "+r["time"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if "LOGIN" in ev and "LOGOUT" not in ev:
                pending_dt = dt
                days_worked.add(r["date"])
            elif "LOGOUT" in ev and pending_dt:
                dur = (dt - pending_dt).total_seconds()
                if 0 < dur < 86400:
                    sess_secs += dur
                    days_worked.add(pending_dt.strftime("%Y-%m-%d"))
                pending_dt = None

        # Active / idle / app classification
        active_s = idle_s = work_s = comms_s = nonwork_s = 0
        app_ctr  = Counter()
        social_monthly  = defaultdict(int)   # platform â†’ secs
        fileshare_monthly = defaultdict(int)

        for ar in app_rows:
            dur_s  = ar["duration_sec"] or 0
            state  = (ar["state"] or "active").lower()
            apn    = ar["app"] or ""
            title  = ar["window_title"] or ""
            al, tl = apn.lower(), title.lower()
            days_worked.add(ar["date"])

            if state == "active":
                active_s += dur_s
                app_ctr[apn] += dur_s
                cat = classify(apn, title)
                if cat == "work":   work_s   += dur_s
                elif cat == "comms": comms_s  += dur_s
                else:               nonwork_s += dur_s
            else:
                idle_s += dur_s

            # Social media detection
            for kw, pname in SOCIAL_MAP.items():
                if kw in al or kw in tl:
                    social_monthly[pname] += dur_s
                    break

            # File sharing detection
            for kw, pname in FILE_SHARE_MAP.items():
                if kw in al or kw in tl:
                    fileshare_monthly[pname] += dur_s
                    break

        total_s = work_s + comms_s + nonwork_s
        work_pct    = round(work_s    / total_s * 100) if total_s else 0
        comms_pct   = round(comms_s   / total_s * 100) if total_s else 0
        nonwork_pct = round(nonwork_s / total_s * 100) if total_s else 0

        top10_apps = [{"app": friendly_name(a), "dur": fmt_secs(s), "s": s}
                      for a, s in app_ctr.most_common(10)]
        top5_apps = top10_apps[:5]

        # Work detail: top work apps
        work_ctr = Counter()
        comms_ctr = Counter()
        for ar in app_rows:
            if (ar["state"] or "active").lower() == "active":
                cat = classify(ar["app"] or "", ar["window_title"] or "")
                if cat == "work":
                    work_ctr[friendly_name(ar["app"] or "Unknown")] += ar["duration_sec"] or 0
                elif cat == "comms":
                    comms_ctr[friendly_name(ar["app"] or "Unknown")] += ar["duration_sec"] or 0
        work_detail  = [{"app": a, "dur": fmt_secs(s)} for a, s in work_ctr.most_common(5)]
        comms_detail = [{"app": a, "dur": fmt_secs(s)} for a, s in comms_ctr.most_common(5)]

        social_alert  = [{"platform": p, "dur": fmt_secs(s), "s": s,
                          "risk": "HIGH" if s>=3600 else "MEDIUM" if s>=1200 else "LOW"}
                         for p, s in sorted(social_monthly.items(), key=lambda x:-x[1])]
        fileshare_alert = [{"platform": p, "dur": fmt_secs(s), "s": s,
                            "risk": "HIGH" if s>=3600 else "MEDIUM" if s>=600 else "LOW"}
                           for p, s in sorted(fileshare_monthly.items(), key=lambda x:-x[1])]

        avg_day_s = active_s / len(days_worked) if days_worked else 0

        results.append({
            "username":       username,
            "computer":       computer,
            "serial":         serial,
            "location":       location,
            "ip":             ip_addr,
            "days_worked":    len(days_worked),
            "sess_hrs":       fmt_secs(sess_secs),
            "active_hrs":     fmt_secs(active_s),
            "active_dec":     fmt_dec(active_s),
            "idle_hrs":       fmt_secs(idle_s),
            "avg_day":        fmt_secs(avg_day_s),
            "work_pct":       work_pct,
            "comms_pct":      comms_pct,
            "nonwork_pct":    nonwork_pct,
            "workPct":        work_pct,
            "commsPct":       comms_pct,
            "nonworkPct":     nonwork_pct,
            "top5_apps":      top5_apps,
            "top10_apps":     top10_apps,
            "topApps":        top10_apps,
            "work_detail":    work_detail,
            "comms_detail":   comms_detail,
            "social_alerts":  social_alert,
            "socialAlerts":   social_alert,
            "fileshare_alerts": fileshare_alert,
            "fileshareAlerts":  fileshare_alert,
            "has_social":     len(social_alert) > 0,
            "has_fileshare":  len(fileshare_alert) > 0,
            "disks":          disks,
            "disk_alert":     any(d["alert"] != "OK" for d in disks),
            "vpn_detected":   len(vpn_rows) > 0,
            "vpn_software":   vpn_rows[0]["software"] if vpn_rows else "",
            "dailyBreakdown": daily_breakdown,
            "daily_breakdown": daily_breakdown,
            "external_email_alerts": external_email_alert,
            "has_external_email": len(external_email_alert) > 0,
            "usb_alerts":     usb_alerts,
            "usbAlerts":      usb_alerts,
            "external_email_alerts": external_email_alert,
            "externalEmailAlerts":   external_email_alert,
            "activeHrs":      fmt_secs(active_s),
            "has_usb":        len(usb_alerts) > 0,
            "active_s":       active_s,
        })

    results.sort(key=lambda x: (-x["active_s"], x["username"]))
    return results


MONTHLY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ company }} | Monthly Summary â€” {{ month_label }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
<style>
  :root {
    --accent: #5b8cff; --accent2: #8b5bff; --text: #e7ecf5; --text-dim: #7e93b5;
    --green: #2dd4a7; --yellow: #f5b942; --red: #ef5a6f;
    --border: rgba(99,141,200,.18); --border-hi: rgba(99,141,200,.35);
  }
  * { box-sizing: border-box; }
  body {
    background: radial-gradient(1200px 700px at 10% -10%, #142250 0%, transparent 60%),
                radial-gradient(1000px 600px at 100% 0%, #2a1a55 0%, transparent 55%),
                linear-gradient(180deg, #0b1224, #070b14 60%);
    color: var(--text); font-family: 'Inter','Segoe UI',sans-serif; min-height: 100vh;
  }
  .topbar { background: rgba(7,11,20,.7); backdrop-filter: blur(14px) saturate(140%);
            border-bottom: 1px solid var(--border); padding: 16px 28px;
            display: flex; justify-content: space-between; align-items: center; }
  .topbar .logo {
    font-size: 1.12rem; font-weight: 800; letter-spacing: -.01em;
    background: linear-gradient(135deg, #8bb0ff 0%, #a855f7 60%, #c084fc 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .topbar .sub { color: var(--text-dim); font-size: .8rem; }
  .month-nav { background: rgba(13,20,36,.55); backdrop-filter: blur(10px);
               border-bottom: 1px solid var(--border); padding: 12px 28px;
               display: flex; gap: 8px; align-items: center; }
  .month-btn { background: rgba(91,140,255,.1); border: 1px solid var(--border);
               color: #b9cdff; padding: 5px 16px; border-radius: 20px; font-size: .82rem;
               cursor: pointer; transition: all .15s ease; }
  .month-btn.active, .month-btn:hover { background: linear-gradient(135deg,var(--accent),var(--accent2)); color: #fff; border-color: transparent; }
  .stat-strip { display: flex; gap: 16px; padding: 20px 28px; flex-wrap: wrap; }
  .stat-box { background: linear-gradient(155deg, rgba(91,140,255,.14), rgba(139,91,255,.06) 60%, rgba(13,20,36,.4));
              border: 1px solid var(--border); border-radius: 14px; padding: 16px 20px;
              min-width: 160px; flex: 1; backdrop-filter: blur(10px); }
  .stat-box .lbl { color: var(--accent); font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; font-weight: 700; }
  .stat-box .val { font-size: 1.85rem; font-weight: 800; margin-top: 4px; letter-spacing: -.02em; }
  .emp-card { background: rgba(22,30,48,.6); backdrop-filter: blur(12px) saturate(130%);
              border: 1px solid var(--border); border-radius: 16px; margin: 0 20px 20px; overflow: hidden;
              box-shadow: 0 8px 30px rgba(0,0,0,.3); }
  .emp-header { background: linear-gradient(90deg, rgba(91,140,255,.12), rgba(139,91,255,.05));
                padding: 16px 20px; display: flex; justify-content: space-between;
                align-items: center; flex-wrap: wrap; gap: 10px; border-bottom: 1px solid var(--border); }
  .emp-name { font-size: 1.08rem; font-weight: 700; color: #c2d6ff; }
  .emp-meta { font-size: .78rem; color: var(--text-dim); margin-top: 2px; }
  .emp-body { padding: 18px 20px; }
  .section-hdr { color: var(--accent); font-size: .7rem; text-transform: uppercase; letter-spacing: .1em;
                 margin-bottom: 10px; border-bottom: 1px solid var(--border); padding-bottom: 6px; font-weight: 700; }
  .stat-pill { display: inline-block; background: rgba(13,20,36,.6); border: 1px solid var(--border);
               border-radius: 9px; padding: 6px 12px; margin: 3px; font-size: .82rem; text-align: center; }
  .stat-pill .p-lbl { color: var(--text-dim); font-size: .68rem; display: block; }
  .stat-pill .p-val { color: var(--text); font-weight: 700; }
  .bar-wrap { background: rgba(13,20,36,.6); border-radius: 8px; height: 20px; overflow: hidden; display: flex; }
  .bar-work { background: linear-gradient(90deg,#1e9e6e,#2dd4a7); }
  .bar-comms { background: linear-gradient(90deg,#3a6fd8,#5b8cff); }
  .bar-nonwork { background: linear-gradient(90deg,#c43e54,#ef5a6f); }
  .bar-lbl { font-size: .72rem; margin-top: 4px; }
  .app-chip { display: inline-block; background: rgba(91,140,255,.08); border: 1px solid var(--border);
              border-radius: 7px; padding: 4px 11px; margin: 2px; font-size: .78rem; color: #aec6f5; }
  .app-chip .app-dur { color: var(--text-dim); margin-left: 4px; }
  .alert-row { display: flex; align-items: center; background: rgba(13,20,36,.55); border: 1px solid var(--border);
               border-radius: 9px; padding: 7px 12px; margin: 3px 0; font-size: .8rem; }
  .alert-row .plat { font-weight: 600; min-width: 110px; }
  .alert-row .dur { color: var(--text-dim); margin-left: 8px; }
  .risk-HIGH   { color: var(--red);    background: rgba(239,90,111,.12); border: 1px solid rgba(239,90,111,.35); padding: 2px 9px; border-radius: 10px; font-size: .7rem; font-weight: 700; }
  .risk-MEDIUM { color: var(--yellow); background: rgba(245,185,66,.12); border: 1px solid rgba(245,185,66,.35); padding: 2px 9px; border-radius: 10px; font-size: .7rem; font-weight: 700; }
  .risk-LOW    { color: var(--green);  background: rgba(45,212,167,.12); border: 1px solid rgba(45,212,167,.35); padding: 2px 9px; border-radius: 10px; font-size: .7rem; font-weight: 700; }
  .no-alert { color: #3f6d56; font-size: .8rem; font-style: italic; }
  .badge-days { background: rgba(91,140,255,.15); color: #b9cdff; border: 1px solid var(--border-hi); padding: 3px 10px; border-radius: 12px; font-size: .78rem; }
  .export-btn { background: rgba(91,140,255,.1); border: 1px solid var(--border); color: #b9cdff;
                padding: 6px 18px; border-radius: 9px; font-size: .82rem; cursor: pointer; text-decoration: none; transition: all .15s ease; }
  .export-btn:hover { background: linear-gradient(135deg,var(--accent),var(--accent2)); color: #fff; border-color: transparent; }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(91,140,255,.25); border-radius: 10px; }
  @media print { .month-nav,.export-btn,.topbar{display:none!important;} .emp-card{break-inside:avoid;} }

  /* â”€â”€ LIGHT THEME â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
  html[data-theme="light"] {
    --border: rgba(60,90,160,.16); --border-hi: rgba(60,90,160,.32);
    --text: #1a2030; --text-dim: #5b6a85;
  }
  html[data-theme="light"] body {
    background: radial-gradient(1200px 700px at 10% -10%, #e8edff 0%, transparent 60%),
                radial-gradient(1000px 600px at 100% 0%, #f1e8ff 0%, transparent 55%),
                linear-gradient(180deg, #f7f9fd, #eef1f8 60%);
    color: var(--text);
  }
  html[data-theme="light"] .topbar { background: rgba(255,255,255,.75); }
  html[data-theme="light"] .month-nav { background: rgba(255,255,255,.55); }
  html[data-theme="light"] .emp-card { background: rgba(255,255,255,.7); box-shadow: 0 8px 24px rgba(30,50,100,.08); }
  html[data-theme="light"] .stat-box { background: linear-gradient(155deg, rgba(91,140,255,.1), rgba(139,91,255,.05) 60%, rgba(255,255,255,.5)); }
  html[data-theme="light"] .month-btn { background: rgba(91,140,255,.08); color: #345; }
  html[data-theme="light"] .app-chip { background: rgba(91,140,255,.07); color: #2a4a8a; }
  html[data-theme="light"] .alert-row { background: rgba(240,244,255,.6); }
  html[data-theme="light"] .no-alert { color: #4a8a6a; }
  html[data-theme="light"] .emp-header { background: linear-gradient(90deg, rgba(91,140,255,.08), rgba(139,91,255,.03)); }
  html[data-theme="light"] .stat-pill { background: rgba(240,244,255,.7); }

  .theme-toggle {
    background: rgba(91,140,255,.1); border: 1px solid var(--border); color: var(--accent);
    width: 34px; height: 34px; border-radius: 50%; cursor: pointer; font-size: .95rem;
    display: inline-flex; align-items: center; justify-content: center; transition: all .15s ease;
  }
  .theme-toggle:hover { background: linear-gradient(135deg,var(--accent),var(--accent2)); color: #fff; }
</style>
<script>
  (function() {
    const saved = localStorage.getItem('empmon_theme') || 'light';
    document.documentElement.setAttribute('data-theme', saved);
  })();
  function toggleTheme() {
    const cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', cur);
    localStorage.setItem('empmon_theme', cur);
    const btn = document.getElementById('themeToggleBtn');
    if (btn) btn.innerHTML = cur === 'light' ? '<i class="fa fa-moon"></i>' : '<i class="fa fa-sun"></i>';
  }
  document.addEventListener('DOMContentLoaded', function() {
    const btn = document.getElementById('themeToggleBtn');
    if (btn) {
      const cur = document.documentElement.getAttribute('data-theme');
      btn.innerHTML = cur === 'light' ? '<i class="fa fa-moon"></i>' : '<i class="fa fa-sun"></i>';
    }
  });
</script>
</head>
<body>

<div class="topbar">
  <div>
    <div class="logo"><i class="fa fa-shield-halved me-2" style="color:#3b82f6;"></i>{{ company }}</div>
    <div class="sub">Monthly Summary Report â€” {{ month_label }}</div>
  </div>
  <div class="d-flex gap-2 align-items-center">
    <a href="/" class="export-btn"><i class="fa fa-gauge me-1"></i>Live Dashboard</a>
    <a href="#" onclick="window.print()" class="export-btn"><i class="fa fa-print me-1"></i>Print / PDF</a>
    <button id="themeToggleBtn" class="theme-toggle" onclick="toggleTheme()" title="Toggle light/dark theme"></button>
  </div>
</div>

<!-- Month selector -->
<div class="month-nav">
  <span style="color:#3a6a9a;font-size:.78rem;margin-right:4px;"><i class="fa fa-calendar me-1"></i>Month:</span>
  {% for m in months %}
  <a href="/monthly/{{ m.val }}" class="month-btn {% if m.val == month_str %}active{% endif %}">{{ m.label }}</a>
  {% endfor %}
</div>

<!-- Stats strip -->
<div class="stat-strip">
  <div class="stat-box">
    <div class="lbl"><i class="fa fa-users me-1"></i>Employees</div>
    <div class="val text-info">{{ employees|length }}</div>
  </div>
  <div class="stat-box">
    <div class="lbl"><i class="fa fa-clock me-1"></i>Total Active Hrs</div>
    <div class="val" style="color:#22c55e;">{{ total_active }}</div>
  </div>
  <div class="stat-box">
    <div class="lbl"><i class="fa fa-calendar-check me-1"></i>Avg Days Worked</div>
    <div class="val" style="color:#60a5fa;">{{ avg_days }}</div>
  </div>
  <div class="stat-box">
    <div class="lbl"><i class="fa fa-triangle-exclamation me-1"></i>Social Media Alerts</div>
    <div class="val" style="color:#ef4444;">{{ social_count }}</div>
  </div>
  <div class="stat-box">
    <div class="lbl"><i class="fa fa-share-nodes me-1"></i>File Share Alerts</div>
    <div class="val" style="color:#f59e0b;">{{ fileshare_count }}</div>
  </div>
</div>

<!-- Employee cards -->
{% for e in employees %}
<div class="emp-card">
  <div class="emp-header">
    <div>
      <div class="emp-name">
        <i class="fa fa-user-circle me-2" style="color:#3b82f6;"></i>{{ e.username }}
        <span class="badge-days ms-2">{{ e.days_worked }} days</span>
        {% if e.has_social %}<span class="ms-2" style="color:#ef4444;font-size:.75rem;"><i class="fa fa-triangle-exclamation me-1"></i>Social Alert</span>{% endif %}
        {% if e.has_fileshare %}<span class="ms-2" style="color:#f59e0b;font-size:.75rem;"><i class="fa fa-share-nodes me-1"></i>File Alert</span>{% endif %}
      </div>
      <div class="emp-meta">
        <i class="fa fa-desktop me-1"></i>{{ e.computer }}
        &nbsp;|&nbsp;<i class="fa fa-barcode me-1"></i>{{ e.serial }}
        &nbsp;|&nbsp;<i class="fa fa-location-dot me-1"></i>{{ e.location }}
        &nbsp;|&nbsp;<i class="fa fa-network-wired me-1"></i>{{ e.ip }}
        {% if e.vpn_detected %}&nbsp;|&nbsp;<span style="color:#f97316;font-weight:600;">&#128274; {{ e.vpn_software if e.vpn_software else "VPN Active" }}</span>{% endif %}
      </div>
    </div>
    <div class="d-flex gap-3 flex-wrap">
      <div class="stat-pill">
        <span class="p-lbl">Session Hrs</span>
        <span class="p-val">{{ e.sess_hrs }}</span>
      </div>
      <div class="stat-pill">
        <span class="p-lbl">Active Hrs</span>
        <span class="p-val" style="color:#22c55e;">{{ e.active_hrs }}</span>
      </div>
      <div class="stat-pill">
        <span class="p-lbl">Idle Hrs</span>
        <span class="p-val" style="color:#f59e0b;">{{ e.idle_hrs }}</span>
      </div>
      <div class="stat-pill">
        <span class="p-lbl">Avg/Day</span>
        <span class="p-val" style="color:#60a5fa;">{{ e.avg_day }}</span>
      </div>
      <div class="stat-pill">
        <span class="p-lbl">Active (dec)</span>
        <span class="p-val">{{ e.active_dec }} hrs</span>
      </div>
    </div>
  </div>

  <div class="emp-body">
    <div class="row g-3">

      <!-- Activity breakdown -->
      <div class="col-md-4">
        <div class="section-hdr"><i class="fa fa-chart-pie me-1"></i>Activity Breakdown</div>
        <div class="bar-wrap mb-1">
          <div class="bar-work"     style="width:{{ e.work_pct }}%"></div>
          <div class="bar-comms"    style="width:{{ e.comms_pct }}%"></div>
          <div class="bar-nonwork"  style="width:{{ e.nonwork_pct }}%"></div>
        </div>
        <div class="bar-lbl">
          <span style="color:#22c55e;">â–ˆ Work {{ e.work_pct }}%</span>&nbsp;&nbsp;
          <span style="color:#60a5fa;">â–ˆ Comms {{ e.comms_pct }}%</span>&nbsp;&nbsp;
          <span style="color:#ef4444;">â–ˆ Non-Work {{ e.nonwork_pct }}%</span>
        </div>
      </div>

      <!-- Top Applications -->
      <div class="col-md-4">
        <div class="section-hdr"><i class="fa fa-window-maximize me-1"></i>Top Applications</div>
        {% for a in e.top5_apps %}
        <div class="app-chip">
          <span style="color:#3a6a9a;margin-right:4px;">{{ loop.index }}</span>
          {{ a.app }}<span class="app-dur">{{ a.dur }}</span>
        </div>
        {% endfor %}
        {% if not e.top5_apps %}<span class="no-alert">No app data</span>{% endif %}
      </div>

      <!-- Social Media Alerts -->
      <div class="col-md-4">
        <div class="section-hdr"><i class="fa fa-mobile-screen me-1"></i>Social Media Alert</div>
        {% for s in e.social_alerts %}
        <div class="alert-row">
          <span class="plat" style="color:#e0a0a0;">{{ s.platform }}</span>
          <span class="dur">{{ s.dur }}</span>
          <span class="ms-auto risk-{{ s.risk }}">{{ s.risk }}</span>
        </div>
        {% endfor %}
        {% if not e.social_alerts %}<div class="no-alert"><i class="fa fa-check me-1" style="color:#22c55e;"></i>No social media detected</div>{% endif %}
      </div>

      <!-- Work Details -->
      <div class="col-md-6">
        <div class="section-hdr"><i class="fa fa-briefcase me-1"></i>Work Details (Top Apps)</div>
        {% for w in e.work_detail %}
        <div class="app-chip">
          <span style="color:#3a9a6a;margin-right:4px;">{{ loop.index }}</span>
          {{ w.app }}<span class="app-dur">{{ w.dur }}</span>
        </div>
        {% endfor %}
        {% if not e.work_detail %}<span class="no-alert">No work app data</span>{% endif %}
      </div>

      <!-- Comms Details -->
      <div class="col-md-6">
        <div class="section-hdr"><i class="fa fa-comments me-1"></i>Comms Details (Top Apps)</div>
        {% for c in e.comms_detail %}
        <div class="app-chip">
          <span style="color:#3a6aaa;margin-right:4px;">{{ loop.index }}</span>
          {{ c.app }}<span class="app-dur">{{ c.dur }}</span>
        </div>
        {% endfor %}
        {% if not e.comms_detail %}<span class="no-alert">No comms app data</span>{% endif %}
      </div>

      <!-- File Sharing Alerts -->
      <div class="col-md-6">
        <div class="section-hdr"><i class="fa fa-share-nodes me-1"></i>File Sharing Alert</div>
        {% if e.fileshare_alerts %}
        {% for f in e.fileshare_alerts %}
        <div class="alert-row">
          <span class="plat" style="color:#e0c060;">{{ f.platform }}</span>
          <span class="dur">{{ f.dur }}</span>
          <span class="ms-auto risk-{{ f.risk }}">{{ f.risk }}</span>
        </div>
        {% endfor %}
        {% else %}
        <span class="no-alert"><i class="fa fa-check me-1" style="color:#22c55e;"></i>No file sharing detected</span>
        {% endif %}
      </div>

      <!-- Drive Storage -->
      <div class="col-md-6">
        <div class="section-hdr"><i class="fa fa-hard-drive me-1"></i>Drive Storage</div>
        {% for d in e.disks %}
        <div class="mb-2">
          <div class="d-flex justify-content-between mb-1" style="font-size:.8rem;">
            <span style="color:#7ab3e0;font-weight:600;">{{ d.drive }}</span>
            <span style="color:{% if d.alert=='HIGH' %}#ef4444{% elif d.alert=='WARN' %}#f97316{% else %}#22c55e{% endif %};">
              {{ d.used_gb }}GB / {{ d.total_gb }}GB
              ({{ d.pct_used }}%{% if d.alert=='HIGH' %} &#9888; FULL{% elif d.alert=='WARN' %} &#9888; LOW{% endif %})
            </span>
          </div>
          <div style="background:#1a3a5c;border-radius:4px;height:8px;overflow:hidden;">
            <div style="width:{{ [d.pct_used,100]|min }}%;height:100%;background:{% if d.alert=='HIGH' %}#ef4444{% elif d.alert=='WARN' %}#f97316{% else %}#3b82f6{% endif %};border-radius:4px;"></div>
          </div>
        </div>
        {% endfor %}
        {% if not e.disks or e.disks[0].total_gb == 0 %}
        <span class="no-alert" style="color:#475569;">No disk data yet â€” will appear after next heartbeat</span>
        {% endif %}
      </div>

      <!-- External / Personal Email Alert -->
      <div class="col-md-6">
        <div class="section-hdr"><i class="fa fa-envelope me-1"></i>External Email Alert</div>
        {% if e.external_email_alerts %}
        {% for em in e.external_email_alerts %}
        <div class="alert-row">
          <span class="plat" style="color:#fca5a5;">{{ em.provider }}</span>
          <span class="dur">{{ em.dur }}</span>
          <span class="ms-auto risk-{{ em.risk }}">{{ em.risk }}</span>
        </div>
        {% endfor %}
        {% else %}
        <span class="no-alert"><i class="fa fa-check me-1" style="color:#22c55e;"></i>No personal email usage detected</span>
        {% endif %}
      </div>

      <!-- USB / Pendrive Alert -->
      <div class="col-md-6">
        <div class="section-hdr"><i class="fa fa-usb me-1"></i>USB / Pendrive Alert</div>
        {% if e.usb_alerts %}
        {% for u in e.usb_alerts %}
        <div class="alert-row">
          <span class="plat" style="color:#fbbf24;">{{ u.label }} ({{ u.size_gb }}GB)</span>
          <span class="dur">{{ u.date }} {{ u.time }}</span>
          <span class="ms-auto risk-HIGH">USB</span>
        </div>
        {% endfor %}
        {% else %}
        <span class="no-alert"><i class="fa fa-check me-1" style="color:#22c55e;"></i>No USB drives detected</span>
        {% endif %}
      </div>

      <!-- Daily Login / Shutdown / Lock / Unlock Breakdown -->
      <div class="col-12">
        <div class="section-hdr"><i class="fa fa-calendar-days me-1"></i>Daily Login / Shutdown / Lock / Unlock</div>
        <div class="table-responsive" style="max-height:260px;overflow-y:auto;">
        <table class="table table-dark-custom mb-0" style="font-size:.78rem;">
          <thead><tr><th>Date</th><th>Login</th><th>Shutdown/Logoff</th><th>Lock Times</th><th>Unlock Times</th></tr></thead>
          <tbody>
          {% for d in e.daily_breakdown %}
          <tr>
            <td>{{ d.date }}</td>
            <td style="color:#22c55e;">{{ d.login }}</td>
            <td style="color:#ef4444;">{{ d.shutdown }}</td>
            <td style="color:#f97316;">{{ d.lock_times if d.lock_times else "--" }}</td>
            <td style="color:#60a5fa;">{{ d.unlock_times if d.unlock_times else "--" }}</td>
          </tr>
          {% endfor %}
          {% if not e.daily_breakdown %}
          <tr><td colspan="5" class="text-center text-muted">No daily data</td></tr>
          {% endif %}
          </tbody>
        </table>
        </div>
      </div>

    </div>
  </div>
</div>
{% endfor %}

{% if not employees %}
<div class="text-center text-muted py-5">
  <i class="fa fa-database fa-2x mb-2 d-block"></i>
  No data found for {{ month_label }}
</div>
{% endif %}

<div style="text-align:center;color:#2a4a6a;font-size:.72rem;padding:20px;">
  {{ company }} Â· Monthly Summary Â· {{ month_label }} Â· Generated {{ now }}
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
</body></html>"""


@app.route("/api/monthly/<month_str>")
def api_monthly(month_str):
    data = build_monthly_summary(month_str)
    return jsonify(data)


@app.route("/monthly")
@app.route("/monthly/<month_str>")
def monthly_summary(month_str=None):
    if not month_str:
        month_str = now_ist().strftime("%Y-%m")

    # Build month selector (last 6 months)
    months = []
    for i in range(6):
        d = (now_ist().replace(day=1) - timedelta(days=i*28)).replace(day=1)
        months.append({"val": d.strftime("%Y-%m"), "label": d.strftime("%b %Y")})

    try:
        month_label = datetime.strptime(month_str, "%Y-%m").strftime("%B %Y")
    except Exception:
        month_label = month_str

    employees = build_monthly_summary(month_str)

    total_active_s  = sum(e["active_s"] for e in employees)
    avg_days        = round(sum(e["days_worked"] for e in employees) / len(employees), 1) if employees else 0
    social_count    = sum(1 for e in employees if e["has_social"])
    fileshare_count = sum(1 for e in employees if e["has_fileshare"])

    return render_template_string(
        MONTHLY_HTML,
        company=COMPANY,
        month_str=month_str,
        month_label=month_label,
        months=months,
        employees=employees,
        total_active=fmt_secs(total_active_s),
        avg_days=avg_days,
        social_count=social_count,
        fileshare_count=fileshare_count,
        now=now_ist().strftime("%Y-%m-%d %H:%M"),
    )


# â”€â”€ MAIN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
init_db()   # must run at import time so gunicorn workers have tables ready

if __name__ == "__main__":
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "localhost"

    print()
    print("=" * 65)
    print(f"  {COMPANY} â€” EmpMon V8 Central Server")
    print("=" * 65)
    print(f"  Database : {DB_PATH}")
    print()
    print(f"  Dashboard:")
    print(f"    Local  : http://localhost:{PORT}")
    print(f"    Network: http://{local_ip}:{PORT}")
    print()
    print(f"  Employee PCs should point to:")
    print(f"    SERVER_URL = 'http://{local_ip}:{PORT}'")
    print(f"    (in employee_agent.py)")
    print()
    print(f"  Press Ctrl+C to stop.")
    print("=" * 65)
    print()

    port = int(os.environ.get("PORT", PORT))
    app.run(host="0.0.0.0", port=port, debug=False)
