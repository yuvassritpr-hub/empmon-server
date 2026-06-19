"""
EmpMon V8 - CENTRAL SERVER
===========================
Receives data from ALL employee PCs via HTTP API.
Stores everything in SQLite.
Serves a live web dashboard showing all employees.

HOW IT WORKS:
  1. Run this on the IT/HR admin PC (or any always-on machine).
  2. Deploy employee_agent.py on each employee PC — it POSTs data here.
  3. Open http://THIS-PC-IP:5000 in any browser to see the dashboard.

INSTALL:
  pip install flask

RUN:
  python central_server.py
"""

import os, json, sqlite3, socket
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from flask import Flask, request, jsonify, render_template_string

# ── CONFIG ─────────────────────────────────────────────────────
COMPANY      = "W-SAFE REINSURANCE"
PORT         = 5000
DB_PATH      = os.path.join(os.path.dirname(__file__), "empmon.db")
IDLE_MIN     = 10
OFFLINE_MIN  = 30
REFRESH_S    = 60
# ───────────────────────────────────────────────────────────────

app = Flask(__name__)


# ── DATABASE ───────────────────────────────────────────────────
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
            CREATE INDEX IF NOT EXISTS idx_raw_date     ON raw_log(date, username);
            CREATE INDEX IF NOT EXISTS idx_raw_user     ON raw_log(username, computer);
            CREATE INDEX IF NOT EXISTS idx_app_date     ON app_log(date, username);
            CREATE INDEX IF NOT EXISTS idx_app_user     ON app_log(username, computer);
        """)
    print(f"[DB] Database ready: {DB_PATH}")


# ── HELPERS ────────────────────────────────────────────────────
def fmt_secs(s):
    if not s or s <= 0:
        return "0h 00m"
    h, m = divmod(int(s // 60), 60)
    return f"{h}h {m:02d}m"


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


# ── API ENDPOINTS ──────────────────────────────────────────────
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

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    """Lightweight heartbeat — keeps employee status fresh without a full event."""
    try:
        data = request.get_json(force=True)
        if not data or not data.get("username"):
            return jsonify({"status": "error"}), 400
        now = datetime.now()
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
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


# ── DASHBOARD DATA BUILDERS ────────────────────────────────────
def get_all_employees_today():
    today = datetime.now().strftime("%Y-%m-%d")
    this_month = datetime.now().strftime("%Y-%m")

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

            # Compute today stats
            first_login = "--"
            last_event_tm = "--"
            last_event_dt = None
            serial = "N/A"
            location = "N/A"
            ip_addr = "N/A"

            for r in today_rows:
                ev = r["event"].upper()
                if "LOGIN" in ev and "LOGOUT" not in ev and first_login == "--":
                    first_login = r["time"]
                last_event_tm = r["time"]
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

            # Status
            status = "Offline"
            if last_event_dt:
                mins_ago = (datetime.now() - last_event_dt).total_seconds() / 60
                last_ev = today_rows[-1]["event"].upper() if today_rows else ""
                if "LOGOUT" in last_ev:
                    status = "Offline"
                elif mins_ago > OFFLINE_MIN:
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
            top_app = app_ctr.most_common(1)[0][0] if app_ctr else "N/A"
            total_active_today += active_today_s

            # Monthly stats
            days_worked = set()
            month_active_s = 0
            for ar in month_app:
                if ar["state"].lower() == "active":
                    month_active_s += ar["duration_sec"] or 0
                    days_worked.add(ar["date"])

            # Monthly session hours (login→logout pairs)
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
                "last_event":      last_event_tm,
                "location":        location,
                "ip":              ip_addr,
                "active_today":    fmt_secs(active_today_s),
                "idle_today":      fmt_secs(idle_today_s),
                "top_app":         top_app,
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
    this_month = datetime.now().strftime("%Y-%m")
    today = datetime.now().strftime("%Y-%m-%d")

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
    last_event_dt = None
    serial = location = ip_addr = "N/A"

    for r in today_raw:
        ev = r["event"].upper()
        if "LOGIN" in ev and "LOGOUT" not in ev and first_login == "--":
            first_login = r["time"]
        last_event = r["time"]
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
        mins_ago = (datetime.now() - last_event_dt).total_seconds() / 60
        last_ev = today_raw[-1]["event"].upper() if today_raw else ""
        if "LOGOUT" in last_ev:
            status = "Offline"
        elif mins_ago > OFFLINE_MIN:
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

    now   = datetime.now()
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
        "first_login": first_login, "last_event": last_event,
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


# ── HTML TEMPLATES ─────────────────────────────────────────────
BASE_STYLE = """
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
<style>
  body { background:#0f1923; color:#e0e6ed; font-family:'Segoe UI',sans-serif; }
  .navbar-dark-custom { background:#091525; border-bottom:2px solid #1a4a7a; }
  .card-dark { background:#162032; border:1px solid #1e3a5f; border-radius:10px; }
  .stat-card { background:linear-gradient(135deg,#1a3a5c,#0d2035); border:1px solid #1e4a7a;
               border-radius:12px; padding:18px 22px; }
  .stat-num  { font-size:2.2rem; font-weight:700; }
  .badge-online  { background:#1a7a3c; color:#fff; padding:4px 10px; border-radius:20px; font-size:.78rem; }
  .badge-idle    { background:#7a6b1a; color:#fff; padding:4px 10px; border-radius:20px; font-size:.78rem; }
  .badge-offline { background:#6b1a1a; color:#fff; padding:4px 10px; border-radius:20px; font-size:.78rem; }
  .table-dark-custom { background:#0d1e30; color:#e0e6ed; }
  .table-dark-custom th { background:#0a192e; color:#7ab3e0; border-color:#1e3a5f; font-size:.82rem; text-transform:uppercase; letter-spacing:.05em; }
  .table-dark-custom td { border-color:#1a2f4a; font-size:.88rem; vertical-align:middle; }
  .table-dark-custom tr:hover td { background:#182840; }
  .dot-online  { display:inline-block; width:9px; height:9px; border-radius:50%; background:#22c55e; margin-right:6px; animation:pulse 2s infinite; }
  .dot-idle    { display:inline-block; width:9px; height:9px; border-radius:50%; background:#eab308; margin-right:6px; }
  .dot-offline { display:inline-block; width:9px; height:9px; border-radius:50%; background:#ef4444; margin-right:6px; }
  @keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:.4;} }
  .btn-detail { background:#1a4a7a; border:none; color:#7ab3e0; font-size:.78rem; padding:3px 10px; border-radius:6px; }
  .btn-detail:hover { background:#1e5a9a; color:#fff; }
  .section-title { color:#7ab3e0; font-size:.75rem; text-transform:uppercase; letter-spacing:.1em; margin-bottom:8px; }
  .refresh-note { font-size:.72rem; color:#4a7a9b; }
  .cal-day { background:#0d2035; border:1px solid #1e3a5f; border-radius:6px; padding:6px 8px;
             margin:2px; min-width:80px; display:inline-block; text-align:center; font-size:.78rem; }
  .cal-day.worked { border-color:#1a6a3c; }
  .cal-day .hrs { font-size:1rem; font-weight:700; color:#22c55e; }
</style>
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
    <span style="color:#7ab3e0;font-size:1.15rem;font-weight:700;">
      <i class="fa fa-shield-halved me-2" style="color:#3b82f6;"></i>{{ company }}
    </span>
    <span class="ms-3" style="color:#4a7a9b;font-size:.82rem;">Employee Monitor Dashboard v8</span>
  </div>
  <div class="d-flex align-items-center gap-3">
    <span class="refresh-note"><i class="fa fa-rotate me-1"></i>Auto-refresh {{ refresh }}s</span>
    <span style="color:#4a7a9b;font-size:.82rem;">{{ now }}</span>
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
      <span style="color:#7ab3e0;font-weight:600;"><i class="fa fa-table me-2"></i>All Employees — Today ({{ today }})</span>
      <input type="text" id="srch" class="form-control form-control-sm w-auto"
             placeholder="Search..." onkeyup="filterTable()"
             style="background:#0d1e30;border-color:#1e3a5f;color:#e0e6ed;min-width:180px;">
    </div>
    <div class="table-responsive">
    <table class="table table-dark-custom table-hover mb-0" id="empTable">
      <thead><tr>
        <th>#</th><th>Username</th><th>Computer</th><th>Serial</th>
        <th>Status</th><th>First Login</th><th>Last Activity</th>
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
        <td>{{ e.first_login }}</td>
        <td><small>{{ e.last_event }}</small></td>
        <td><strong style="color:#22c55e;">{{ e.active_today }}</strong></td>
        <td><small class="text-muted">{{ e.idle_today }}</small></td>
        <td><small>{{ e.top_app }}</small></td>
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
    <span style="color:#7ab3e0;font-size:1.15rem;font-weight:700;">
      <i class="fa fa-shield-halved me-2" style="color:#3b82f6;"></i>{{ company }}
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
        <div class="section-title mb-3"><i class="fa fa-calendar me-1"></i>This Month — Daily Active Hours</div>
        <div>
        {% for d in e.cal %}
          <div class="cal-day {% if d.worked %}worked{% endif %}">
            <div style="color:#4a7a9b;font-size:.7rem;">{{ d.day }}</div>
            {% if d.worked %}
              <div class="hrs">{{ d.dec }}</div>
              <div style="color:#4a9b6a;font-size:.68rem;">{{ d.active }}</div>
            {% else %}
              <div style="color:#3a4a5a;font-size:.85rem;">—</div>
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
              {% if 'LOGIN' in r.event and 'LOGOUT' not in r.event %}
                <span style="color:#22c55e;">{{ r.event }}</span>
              {% elif 'LOGOUT' in r.event %}
                <span style="color:#ef4444;">{{ r.event }}</span>
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


# ── ROUTES ─────────────────────────────────────────────────────
@app.route("/")
def index():
    rows, online, idle_cnt, offline, total_active = get_all_employees_today()
    today   = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template_string(
        INDEX_HTML,
        company=COMPANY, refresh=REFRESH_S, now=now_str, today=today,
        total=len(rows), online=online, idle=idle_cnt,
        employees=list(enumerate(rows, 1)),
        total_active=fmt_secs(total_active),
        db_path=DB_PATH, idle_min=IDLE_MIN, offline_min=OFFLINE_MIN,
    )


@app.route("/employee/<username>/<computer>")
def employee_detail(username, computer):
    e       = get_employee_detail(username, computer)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template_string(
        DETAIL_HTML, company=COMPANY, refresh=REFRESH_S, now=now_str, e=e)


@app.route("/api/summary")
def api_summary():
    rows, online, idle_cnt, offline, total_active = get_all_employees_today()
    return jsonify({
        "generated": datetime.now().isoformat(),
        "total": len(rows), "online": online,
        "idle": idle_cnt, "offline": offline,
        "total_active_today_hrs": fmt_dec(total_active),
        "employees": rows,
    })


@app.route("/api/status")
def api_status():
    return jsonify({"status": "ok", "server": COMPANY, "version": "8.0"})


# ── MAIN ───────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "localhost"

    print()
    print("=" * 65)
    print(f"  {COMPANY} — EmpMon V8 Central Server")
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
