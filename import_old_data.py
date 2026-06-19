"""
EmpMon V8 - IMPORT OLD DATA
============================
Imports existing raw_log.csv and app_log.csv files from the old
OneDrive-based EmpMon V7 system into the new SQLite database.

Usage:
  python import_old_data.py

It will scan the OneDrive EmpMonData folder and import all employees.
"""

import os, csv, sqlite3, glob
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "empmon.db")


def _find_onedrive():
    home = os.path.expanduser("~")
    od = os.environ.get("OneDriveCommercial", "")
    if od and os.path.isdir(od):
        return od
    od = os.environ.get("OneDrive", "")
    if od and os.path.isdir(od):
        return od
    try:
        for name in sorted(os.listdir(home)):
            full = os.path.join(home, name)
            if name.lower().startswith("onedrive") and os.path.isdir(full):
                return full
    except Exception:
        pass
    return os.path.join(home, "OneDrive - PRIDE GLOBAL")


def parse_dur(val):
    try:
        v = str(val).strip()
        if ":" in v:
            p = v.split(":")
            return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
        return int(float(v or 0))
    except Exception:
        return 0


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def import_folder(conn, folder_path, folder_name):
    parts    = folder_name.split("_", 1)
    username = parts[0] if parts else folder_name
    computer = parts[1] if len(parts) > 1 else "N/A"

    raw_csv = os.path.join(folder_path, "raw_log.csv")
    app_csv = os.path.join(folder_path, "app_log.csv")

    raw_count = 0
    app_count = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if os.path.exists(raw_csv):
        try:
            with open(raw_csv, "r", encoding="utf-8", errors="ignore") as f:
                for r in csv.DictReader(f):
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO raw_log
                              (date,time,event,username,computer,serial,ip,city,region,country,lat,lon,received_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (
                            r.get("Date",""), r.get("Time",""), r.get("Event",""),
                            r.get("Username", username), r.get("Computer", computer),
                            r.get("SerialNumber", "N/A"),
                            r.get("IP", "N/A"), r.get("City", "N/A"),
                            r.get("Region", "N/A"), r.get("Country", "IN"),
                            r.get("Lat", "N/A"), r.get("Lon", "N/A"),
                            now
                        ))
                        raw_count += 1
                    except Exception as e:
                        pass
        except Exception as e:
            print(f"  raw_log read error: {e}")

    if os.path.exists(app_csv):
        try:
            with open(app_csv, "r", encoding="utf-8", errors="ignore") as f:
                for r in csv.DictReader(f):
                    try:
                        dur_s = parse_dur(r.get("Duration", r.get("DurationSec", 0)))
                        conn.execute("""
                            INSERT OR IGNORE INTO app_log
                              (date,start_time,end_time,username,computer,app,window_title,duration_sec,state,received_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?)
                        """, (
                            r.get("Date",""), r.get("Start","00:00:00"), r.get("End","00:00:00"),
                            r.get("Username", username), r.get("Computer", computer),
                            r.get("App",""), r.get("WindowTitle",""), dur_s,
                            r.get("State","active"), now
                        ))
                        app_count += 1
                    except Exception:
                        pass
        except Exception as e:
            print(f"  app_log read error: {e}")

    return raw_count, app_count


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found: {DB_PATH}")
        print("Run central_server.py first to create the database.")
        return

    od_base = os.path.join(_find_onedrive(), "EmpMonData")
    if not os.path.exists(od_base):
        print(f"OneDrive EmpMonData not found: {od_base}")
        print("You can also specify a path manually by editing this script.")
        return

    print(f"\nImporting from: {od_base}")
    print(f"Into database:  {DB_PATH}\n")

    folders = [f for f in os.listdir(od_base)
               if os.path.isdir(os.path.join(od_base, f))]

    if not folders:
        print("No employee folders found.")
        return

    total_raw = total_app = 0
    with get_db() as conn:
        for folder in sorted(folders):
            path = os.path.join(od_base, folder)
            raw_n, app_n = import_folder(conn, path, folder)
            print(f"  {folder:<35} raw={raw_n:>5}  app={app_n:>5}")
            total_raw += raw_n
            total_app += app_n

    print(f"\nDone. Imported {total_raw} raw events + {total_app} app segments.")
    print("Refresh the dashboard to see the data.")


if __name__ == "__main__":
    main()
