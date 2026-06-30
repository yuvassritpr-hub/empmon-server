// W-SAFE REINSURANCE — Real-time Employee Monitor Server
// Node.js + Express + Socket.IO + SQLite (same DB as the Python agent writes events to)

const express = require("express");
const http = require("http");
const cors = require("cors");
const { Server } = require("socket.io");
const Database = require("better-sqlite3");
const path = require("path");

const PORT = process.env.PORT || 5050;
const DB_PATH = path.join(__dirname, "..", "..", "empmon.db");

const app = express();
app.use(cors());
app.use(express.json());

const server = http.createServer(app);
const io = new Server(server, { cors: { origin: "*" } });

const db = new Database(DB_PATH);
db.pragma("journal_mode = WAL");

db.exec(`
  CREATE TABLE IF NOT EXISTS raw_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, time TEXT, event TEXT, username TEXT, computer TEXT,
    serial TEXT DEFAULT 'N/A', ip TEXT DEFAULT 'N/A', city TEXT DEFAULT 'N/A',
    region TEXT DEFAULT 'N/A', country TEXT DEFAULT 'IN',
    lat TEXT DEFAULT 'N/A', lon TEXT DEFAULT 'N/A', received_at TEXT
  );
  CREATE TABLE IF NOT EXISTS app_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, start_time TEXT, end_time TEXT, username TEXT, computer TEXT,
    app TEXT, window_title TEXT DEFAULT '', duration_sec INTEGER DEFAULT 0,
    state TEXT DEFAULT 'active', received_at TEXT
  );
  CREATE TABLE IF NOT EXISTS disk_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, time TEXT, username TEXT, computer TEXT, drive TEXT,
    total_gb REAL, used_gb REAL, free_gb REAL, pct_used REAL, received_at TEXT
  );
  CREATE TABLE IF NOT EXISTS vpn_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, time TEXT, username TEXT, computer TEXT,
    vpn_on INTEGER, software TEXT, adapter TEXT, received_at TEXT
  );
  CREATE TABLE IF NOT EXISTS usb_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, time TEXT, username TEXT, computer TEXT,
    drive TEXT, label TEXT, size_gb REAL, action TEXT DEFAULT 'connected', received_at TEXT
  );
  CREATE TABLE IF NOT EXISTS browser_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, time TEXT, username TEXT, computer TEXT,
    domain TEXT, secs INTEGER DEFAULT 0, received_at TEXT
  );
`);

const nowStr = () => new Date().toISOString().slice(0, 19).replace("T", " ");
const today = () => new Date().toISOString().slice(0, 10);
const hms = () => new Date().toTimeString().slice(0, 8);

// ── Broadcast helper: push fresh employee snapshot to all connected dashboards ──
function broadcastEmployees() {
  const employees = buildLiveSnapshot();
  io.emit("employees:update", employees);
}

function buildLiveSnapshot() {
  const emps = db.prepare("SELECT DISTINCT username, computer FROM raw_log ORDER BY username").all();
  const t = today();

  return emps.map((e) => {
    const rows = db.prepare(
      "SELECT * FROM raw_log WHERE username=? AND computer=? AND date=? ORDER BY time"
    ).all(e.username, e.computer, t);

    const last = rows[rows.length - 1];
    let status = "Offline";
    if (last) {
      const lastDt = new Date(`${t}T${last.time}`);
      const minsAgo = (Date.now() - lastDt.getTime()) / 60000;
      const ev = last.event.toUpperCase();
      if (["LOGOUT(SHUTDOWN)", "LOGOUT(LOGOFF)", "LOGOUT(LOCK)", "LOGOUT(SCREEN-OFF)"].includes(ev)) {
        status = "Offline";
      } else if (ev === "LOGOUT(IDLE)") {
        status = "Idle";
      } else if (minsAgo > 30) status = "Offline";
      else if (minsAgo > 10) status = "Idle";
      else status = "Online";
    }

    const vpnRow = db.prepare(
      "SELECT * FROM vpn_log WHERE username=? AND computer=? ORDER BY date DESC, time DESC LIMIT 1"
    ).get(e.username, e.computer);

    const diskRow = db.prepare(
      "SELECT * FROM disk_log WHERE username=? AND computer=? ORDER BY date DESC, time DESC LIMIT 1"
    ).get(e.username, e.computer);

    const appRows = db.prepare(
      "SELECT app, window_title, SUM(duration_sec) as total FROM app_log WHERE username=? AND computer=? AND date=? AND state='active' GROUP BY app ORDER BY total DESC LIMIT 20"
    ).all(e.username, e.computer, t);

    // Browser tab tracking — parse window titles from Chrome/Edge/Firefox/Brave
    const browserApps = /chrome|msedge|edge|firefox|brave|opera/i;
    const tabRaws = db.prepare(
      "SELECT app, window_title, duration_sec FROM app_log WHERE username=? AND computer=? AND date=? AND state='active'"
    ).all(e.username, e.computer, t);
    const tabMap = {};
    for (const r of tabRaws) {
      if (!browserApps.test(r.app || "")) continue;
      const title = (r.window_title || "").trim();
      // Strip browser suffix: "Page Title - Google Chrome" → "Page Title"
      const clean = title.replace(/\s*[-–|]\s*(Google Chrome|Microsoft Edge|Mozilla Firefox|Brave|Opera|Chrome).*$/i, "").trim();
      if (!clean || clean.length < 3 || /^(new tab|newtab)$/i.test(clean)) continue;
      tabMap[clean] = (tabMap[clean] || 0) + (r.duration_sec || 0);
    }
    const topTabs = Object.entries(tabMap).sort((a,b)=>b[1]-a[1]).slice(0,10).map(([title,secs])=>({title,secs}));

    // Work / Comms / Non-work classification
    let workSecs = 0, commsSecs = 0, nonworkSecs = 0;
    const workApps = {}, commsApps = {};
    for (const ar of appRows) {
      const dur = ar.total || 0;
      const cat = classify(ar.app, ar.window_title);
      if (cat === "work") { workSecs += dur; workApps[ar.app] = (workApps[ar.app] || 0) + dur; }
      else if (cat === "comms") { commsSecs += dur; commsApps[ar.app] = (commsApps[ar.app] || 0) + dur; }
      else nonworkSecs += dur;
    }
    const totalSecs = workSecs + commsSecs + nonworkSecs;
    const workPct = totalSecs ? Math.round((workSecs / totalSecs) * 100) : 0;
    const commsPct = totalSecs ? Math.round((commsSecs / totalSecs) * 100) : 0;
    const nonworkPct = totalSecs ? Math.round((nonworkSecs / totalSecs) * 100) : 0;
    const workAppList = Object.entries(workApps).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([app,s])=>({app,secs:s}));
    const commsAppList = Object.entries(commsApps).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([app,s])=>({app,secs:s}));

    // Today login / shutdown times
    const isLogin = (ev) => ev.toUpperCase().startsWith("LOGIN");
    const isShutdown = (ev) => ["LOGOUT(SHUTDOWN)", "LOGOUT(LOGOFF)"].includes(ev.toUpperCase());
    const todayLogin = rows.find((r) => isLogin(r.event))?.time || null;
    const todayShutdown = [...rows].reverse().find((r) => isShutdown(r.event))?.time || null;

    // Previous day login time
    const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
    const yd = yesterday.toISOString().slice(0, 10);
    const prevRows = db.prepare(
      "SELECT * FROM raw_log WHERE username=? AND computer=? AND date=? ORDER BY time"
    ).all(e.username, e.computer, yd);
    const prevDayLogin = prevRows.find((r) => isLogin(r.event))?.time || null;

    // Today's top browser sites
    const siteRows = db.prepare(
      "SELECT domain, MAX(secs) as secs FROM browser_log WHERE username=? AND computer=? AND date=? GROUP BY domain ORDER BY secs DESC LIMIT 10"
    ).all(e.username, e.computer, t);
    const topSites = siteRows.map((r) => ({ domain: r.domain, secs: r.secs }));

    return {
      username: e.username,
      computer: e.computer,
      serial: rows.find((r) => r.serial !== "N/A")?.serial || "N/A",
      location: rows.find((r) => r.city !== "N/A")?.city || "N/A",
      ip: rows.find((r) => r.ip !== "N/A")?.ip || "N/A",
      status,
      lastEvent: last ? last.time : "--",
      todayLogin,
      todayShutdown,
      prevDayLogin,
      vpn: vpnRow && vpnRow.vpn_on && !vpnRow.software?.startsWith("REMOTE:") ? (vpnRow.software || "VPN Active") : null,
      remoteApp: vpnRow && vpnRow.software?.startsWith("REMOTE:") ? vpnRow.software.replace("REMOTE:", "") : null,
      disk: diskRow
        ? { drive: diskRow.drive, usedGb: diskRow.used_gb, totalGb: diskRow.total_gb, freeGb: (diskRow.total_gb - diskRow.used_gb).toFixed(1), pctUsed: diskRow.pct_used }
        : null,
      topApps: appRows.slice(0,5).map((a) => ({ app: a.app, secs: a.total })),
      workPct, commsPct, nonworkPct,
      totalSecs,
      workApps: workAppList,
      commsApps: commsAppList,
      topSites,
      topTabs,
    };
  });
}

// ── Classification maps (mirrors central_server.py) ──────────────
const SOCIAL_MAP = {
  youtube: "YouTube", instagram: "Instagram", facebook: "Facebook",
  whatsapp: "WhatsApp", twitter: "Twitter/X", "x.com": "Twitter/X",
  tiktok: "TikTok", snapchat: "Snapchat", linkedin: "LinkedIn",
  reddit: "Reddit", telegram: "Telegram", netflix: "Netflix",
  hotstar: "Hotstar", spotify: "Spotify", discord: "Discord",
  threads: "Threads", gmail: "Gmail", "mail.google": "Gmail",
};
const FILE_SHARE_MAP = {
  whatsapp: "WhatsApp", telegram: "Telegram", gmail: "Gmail",
  "mail.google": "Gmail", onedrive: "OneDrive", sharepoint: "SharePoint",
  dropbox: "Dropbox", "google drive": "Google Drive", "drive.google": "Google Drive",
  wetransfer: "WeTransfer", filezilla: "FileZilla", winscp: "WinSCP",
  "box.com": "Box", "mega.nz": "Mega", anydesk: "AnyDesk", teamviewer: "TeamViewer",
};
const EXTERNAL_EMAIL_MAP = {
  "mail.google.com": "Gmail", "gmail.com": "Gmail",
  "outlook.live.com": "Outlook.com (Personal)", "hotmail.com": "Hotmail",
  "mail.yahoo.com": "Yahoo Mail", "yahoo.com": "Yahoo Mail",
  "rediffmail.com": "Rediffmail", "protonmail.com": "ProtonMail",
  "icloud.com": "iCloud Mail", "aol.com": "AOL Mail",
};
const COMMS_KEYS = ["outlook", "teams", "ms-teams", "zoom", "slack", "skype", "webex",
  "thunderbird", "whatsapp", "telegram", "meetgeek", "lync", "gmail", "discord"];
const WORK_KEYS = ["excel", "winword", "powerpnt", "onenote", "acrobat", "adobe",
  "notepad", "mstsc", "sap", "tally", "code", "explorer", "onedrive", "sharepoint"];

function classify(app, title) {
  const al = (app || "").toLowerCase();
  const tl = (title || "").toLowerCase();
  if (COMMS_KEYS.some((k) => al.includes(k))) return "comms";
  if (["chrome", "firefox", "msedge", "edge"].some((b) => al.includes(b))) {
    if (["gmail", "teams", "zoom", "outlook"].some((k) => tl.includes(k))) return "comms";
    if (Object.keys(SOCIAL_MAP).some((k) => tl.includes(k))) return "nonwork";
    return "work";
  }
  if (WORK_KEYS.some((k) => al.includes(k))) return "work";
  return "work";
}

function fmtSecs(s) {
  if (!s) return "0h 00m 00s";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  return `${h}h ${String(m).padStart(2, "0")}m ${String(sec).padStart(2, "0")}s`;
}

function buildMonthlySummary(monthStr) {
  const emps = db.prepare("SELECT DISTINCT username, computer FROM raw_log ORDER BY username").all();
  const results = [];

  for (const emp of emps) {
    const { username, computer } = emp;
    const raw = db.prepare(
      "SELECT * FROM raw_log WHERE username=? AND computer=? AND date LIKE ? ORDER BY date,time"
    ).all(username, computer, `${monthStr}%`);
    const appRows = db.prepare(
      "SELECT * FROM app_log WHERE username=? AND computer=? AND date LIKE ?"
    ).all(username, computer, `${monthStr}%`);

    if (!raw.length && !appRows.length) continue;

    // Daily login/shutdown/lock/unlock + per-day work & idle
    const dailyMap = {};
    const dayFirstLast = {};
    for (const r of raw) {
      const ev = r.event.toUpperCase();
      const d = r.date;
      if (!dailyMap[d]) dailyMap[d] = { login: "--", shutdown: "--", lock: [], unlock: [], idleCount: 0 };
      if (ev.startsWith("LOGIN") && dailyMap[d].login === "--") dailyMap[d].login = r.time;
      else if (["LOGOUT(SHUTDOWN)", "LOGOUT(LOGOFF)"].includes(ev)) dailyMap[d].shutdown = r.time;
      else if (ev === "LOGOUT(LOCK)") dailyMap[d].lock.push(r.time);
      else if (ev === "LOGIN(UNLOCK)") dailyMap[d].unlock.push(r.time);
      else if (ev === "LOGOUT(IDLE)") dailyMap[d].idleCount++;
      if (!dayFirstLast[d]) dayFirstLast[d] = { first: r.time, last: r.time };
      else dayFirstLast[d].last = r.time;
    }

    // Per-day active app seconds
    const dayAppSecs = {};
    const dayWorkSecs = {}, dayCommsSecs = {}, dayNonworkSecs = {};

    // App classification, social/file-share/email, top apps
    const appCtr = {};
    let workS = 0, commsS = 0, nonworkS = 0;
    const social = {}, fileshare = {}, extEmail = {};
    for (const ar of appRows) {
      if ((ar.state || "active") !== "active") continue;
      const dur = ar.duration_sec || 0;
      const al = (ar.app || "").toLowerCase();
      const tl = (ar.window_title || "").toLowerCase();
      appCtr[ar.app] = (appCtr[ar.app] || 0) + dur;
      dayAppSecs[ar.date] = (dayAppSecs[ar.date] || 0) + dur;
      const cat = classify(ar.app, ar.window_title);
      if (cat === "work") { workS += dur; dayWorkSecs[ar.date] = (dayWorkSecs[ar.date] || 0) + dur; }
      else if (cat === "comms") { commsS += dur; dayCommsSecs[ar.date] = (dayCommsSecs[ar.date] || 0) + dur; }
      else { nonworkS += dur; dayNonworkSecs[ar.date] = (dayNonworkSecs[ar.date] || 0) + dur; }
      for (const [kw, name] of Object.entries(SOCIAL_MAP)) if (al.includes(kw) || tl.includes(kw)) { social[name] = (social[name] || 0) + dur; break; }
      for (const [kw, name] of Object.entries(FILE_SHARE_MAP)) if (al.includes(kw) || tl.includes(kw)) { fileshare[name] = (fileshare[name] || 0) + dur; break; }
      for (const [kw, name] of Object.entries(EXTERNAL_EMAIL_MAP)) if (al.includes(kw) || tl.includes(kw)) { extEmail[name] = (extEmail[name] || 0) + dur; break; }
    }
    const dailyBreakdown = Object.entries(dailyMap).sort().map(([date, v]) => {
      const activeSecs = dayAppSecs[date] || 0;
      const wSecs = dayWorkSecs[date] || 0;
      const cSecs = dayCommsSecs[date] || 0;
      const fl = dayFirstLast[date];
      let sessionSecs = 0;
      if (fl && fl.first !== fl.last) {
        const s = new Date(`${date}T${fl.first}`);
        const en = new Date(`${date}T${fl.last}`);
        sessionSecs = Math.max(0, (en - s) / 1000);
      }
      const idleSecs = Math.max(0, sessionSecs - activeSecs);
      return {
        date,
        login: v.login,
        shutdown: v.shutdown,
        lockCount: v.lock.length,
        unlockCount: v.unlock.length,
        lockTimes: v.lock.slice(0, 3).join(", "),
        unlockTimes: v.unlock.slice(0, 3).join(", "),
        activeSecs,
        workSecs: wSecs,
        commsSecs: cSecs,
        idleSecs,
        sessionSecs,
      };
    });

    const totalS = workS + commsS + nonworkS;
    const topApps = Object.entries(appCtr).sort((a, b) => b[1] - a[1]).slice(0, 10)
      .map(([app, s]) => ({ app, dur: fmtSecs(s) }));
    const riskLevel = (s, hi, mid) => (s >= hi ? "HIGH" : s >= mid ? "MEDIUM" : "LOW");
    const socialAlerts = Object.entries(social).sort((a, b) => b[1] - a[1])
      .map(([platform, s]) => ({ platform, dur: fmtSecs(s), risk: riskLevel(s, 3600, 1200) }));
    const fileshareAlerts = Object.entries(fileshare).sort((a, b) => b[1] - a[1])
      .map(([platform, s]) => ({ platform, dur: fmtSecs(s), risk: riskLevel(s, 3600, 600) }));
    const externalEmailAlerts = Object.entries(extEmail).sort((a, b) => b[1] - a[1])
      .map(([provider, s]) => ({ provider, dur: fmtSecs(s), risk: riskLevel(s, 3600, 600) }));

    // Serial / location / IP
    let serial = "N/A", location = "N/A", ip = "N/A";
    for (const r of raw) {
      if (r.serial && r.serial !== "N/A") serial = r.serial;
      if (r.city && r.city !== "N/A") location = `${r.city}, ${r.region || ""}`.replace(/, $/, "");
      if (r.ip && r.ip !== "N/A") ip = r.ip;
    }

    // USB alerts
    const usbRows = db.prepare(
      "SELECT * FROM usb_log WHERE username=? AND computer=? AND date LIKE ? ORDER BY date DESC,time DESC LIMIT 20"
    ).all(username, computer, `${monthStr}%`);
    const seenUsb = new Set();
    const usbAlerts = [];
    for (const u of usbRows) {
      const key = `${u.drive}|${u.label}`;
      if (!seenUsb.has(key)) { seenUsb.add(key); usbAlerts.push({ drive: u.drive, label: u.label || u.drive, sizeGb: u.size_gb, date: u.date, time: u.time }); }
    }

    results.push({
      username, computer, serial, location, ip,
      workPct: totalS ? Math.round((workS / totalS) * 100) : 0,
      commsPct: totalS ? Math.round((commsS / totalS) * 100) : 0,
      nonworkPct: totalS ? Math.round((nonworkS / totalS) * 100) : 0,
      activeHrs: fmtSecs(workS + commsS + nonworkS),
      topApps, socialAlerts, fileshareAlerts, externalEmailAlerts, usbAlerts, dailyBreakdown,
    });
  }
  return results;
}

// ── API endpoints (same contract as the Python agent already uses) ──
app.post("/api/event", (req, res) => {
  const d = req.body;
  db.prepare(`
    INSERT INTO raw_log (date,time,event,username,computer,serial,ip,city,region,country,lat,lon,received_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(d.date, d.time, d.event, d.username, d.computer, d.serial || "N/A",
         d.ip || "N/A", d.city || "N/A", d.region || "N/A", d.country || "IN",
         d.lat || "N/A", d.lon || "N/A", nowStr());
  broadcastEmployees();
  res.json({ status: "ok" });
});

app.post("/api/app_event", (req, res) => {
  const d = req.body;
  db.prepare(`
    INSERT INTO app_log (date,start_time,end_time,username,computer,app,window_title,duration_sec,state,received_at)
    VALUES (?,?,?,?,?,?,?,?,?,?)
  `).run(d.date, d.start_time, d.end_time, d.username, d.computer, d.app,
         d.window_title || "", d.duration || 0, d.state || "active", nowStr());
  broadcastEmployees();
  res.json({ status: "ok" });
});

app.post("/api/heartbeat", (req, res) => {
  const d = req.body;
  db.prepare(`
    INSERT INTO raw_log (date,time,event,username,computer,serial,ip,city,region,country,lat,lon,received_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
  `).run(today(), hms(), "HEARTBEAT", d.username, d.computer || "N/A",
         d.serial || "N/A", d.ip || "N/A", d.city || "N/A", d.region || "N/A",
         d.country || "IN", "N/A", "N/A", nowStr());

  if (Array.isArray(d.disks)) {
    for (const disk of d.disks) {
      db.prepare(`
        INSERT INTO disk_log (date,time,username,computer,drive,total_gb,used_gb,free_gb,pct_used,received_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
      `).run(today(), hms(), d.username, d.computer || "N/A", disk.drive || "C:",
             disk.total_gb || 0, disk.used_gb || 0, disk.free_gb || 0, disk.pct_used || 0, nowStr());
    }
  }
  if (d.vpn) {
    db.prepare(`
      INSERT INTO vpn_log (date,time,username,computer,vpn_on,software,adapter,received_at)
      VALUES (?,?,?,?,?,?,?,?)
    `).run(today(), hms(), d.username, d.computer || "N/A",
           d.vpn.connected ? 1 : 0, (d.vpn.software || []).join(", "), d.vpn.adapter || "", nowStr());
  }
  if (Array.isArray(d.remote_apps) && d.remote_apps.length > 0) {
    db.prepare(`
      INSERT INTO vpn_log (date,time,username,computer,vpn_on,software,adapter,received_at)
      VALUES (?,?,?,?,?,?,?,?)
    `).run(today(), hms(), d.username, d.computer || "N/A",
           1, "REMOTE:" + d.remote_apps.join(", "), "remote_desktop", nowStr());
  }
  if (Array.isArray(d.browser_sites)) {
    for (const s of d.browser_sites) {
      db.prepare(`
        INSERT INTO browser_log (date,time,username,computer,domain,secs,received_at)
        VALUES (?,?,?,?,?,?,?)
      `).run(today(), hms(), d.username, d.computer || "N/A",
             s.domain || "", s.secs || 0, nowStr());
    }
  }
  if (Array.isArray(d.usb_drives)) {
    for (const u of d.usb_drives) {
      db.prepare(`
        INSERT INTO usb_log (date,time,username,computer,drive,label,size_gb,action,received_at)
        VALUES (?,?,?,?,?,?,?,?,?)
      `).run(today(), hms(), d.username, d.computer || "N/A",
             u.drive || "", u.label || "", u.size_gb || 0, "connected", nowStr());
    }
  }
  broadcastEmployees();
  res.json({ status: "ok" });
});

app.post("/api/batch", (req, res) => {
  const { events = [], app_events = [] } = req.body;
  const insertEvent = db.prepare(`
    INSERT INTO raw_log (date,time,event,username,computer,serial,ip,city,region,country,lat,lon,received_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
  `);
  const insertApp = db.prepare(`
    INSERT INTO app_log (date,start_time,end_time,username,computer,app,window_title,duration_sec,state,received_at)
    VALUES (?,?,?,?,?,?,?,?,?,?)
  `);
  for (const d of events) {
    insertEvent.run(d.date, d.time, d.event, d.username, d.computer, d.serial || "N/A",
      d.ip || "N/A", d.city || "N/A", d.region || "N/A", d.country || "IN",
      d.lat || "N/A", d.lon || "N/A", nowStr());
  }
  for (const d of app_events) {
    insertApp.run(d.date, d.start_time, d.end_time, d.username, d.computer, d.app,
      d.window_title || "", d.duration || 0, d.state || "active", nowStr());
  }
  broadcastEmployees();
  res.json({ status: "ok", inserted: events.length + app_events.length });
});

app.get("/api/status", (req, res) => {
  res.json({ server: "W-SAFE REINSURANCE", status: "ok", version: "realtime-1.0" });
});

app.get("/api/employees", (req, res) => {
  res.json(buildLiveSnapshot());
});

app.get("/api/monthly/:month", (req, res) => {
  res.json(buildMonthlySummary(req.params.month));
});

io.on("connection", (socket) => {
  console.log("Dashboard connected:", socket.id);
  socket.emit("employees:update", buildLiveSnapshot());
  socket.on("disconnect", () => console.log("Dashboard disconnected:", socket.id));
});

// Periodic refresh in case nothing posts for a while (keeps Idle/Offline status accurate)
setInterval(broadcastEmployees, 30000);

server.listen(PORT, "0.0.0.0", () => {
  console.log(`W-SAFE Realtime Server running on http://0.0.0.0:${PORT}`);
  console.log(`DB: ${DB_PATH}`);
});
