import { useEffect, useState } from "react";
import { io } from "socket.io-client";
import MonthlyReport from "./MonthlyReport";
import "./App.css";

const SOCKET_URL = "http://localhost:5050";

function fmtSecs(s) {
  if (!s || s === 0) return "0m";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${String(m).padStart(2,"0")}m`;
  return `${m}m`;
}

function initials(name) {
  return (name || "?").slice(0, 2).toUpperCase();
}

function StatusDot({ status }) {
  const map = {
    Online:  { color: "#10b981", label: "Online"  },
    Idle:    { color: "#f59e0b", label: "Idle"    },
    Offline: { color: "#ef4444", label: "Offline" },
  };
  const s = map[status] || map.Offline;
  return (
    <span className="status-dot-wrap">
      <span className="status-dot" style={{ background: s.color, boxShadow: status === "Online" ? `0 0 8px ${s.color}` : "none" }} />
      <span className="status-label" style={{ color: s.color }}>{s.label}</span>
    </span>
  );
}

function ActivityBar({ workPct, commsPct, nonworkPct }) {
  return (
    <div className="activity-bar-wrap">
      <div className="activity-bar">
        {workPct > 0 && <div className="ab-seg work" style={{ width: `${workPct}%` }} title={`Work ${workPct}%`} />}
        {commsPct > 0 && <div className="ab-seg comms" style={{ width: `${commsPct}%` }} title={`Comms ${commsPct}%`} />}
        {nonworkPct > 0 && <div className="ab-seg nonwork" style={{ width: `${nonworkPct}%` }} title={`Non-work ${nonworkPct}%`} />}
      </div>
      <div className="ab-legend">
        <span className="ab-dot work-dot" /> Work <strong>{workPct}%</strong>
        <span className="ab-dot comms-dot" /> Comms <strong>{commsPct}%</strong>
        <span className="ab-dot nonwork-dot" /> Other <strong>{nonworkPct}%</strong>
      </div>
    </div>
  );
}

function AppList({ apps, color, emptyMsg }) {
  if (!apps || !apps.length) return <div className="no-data">{emptyMsg || "No data"}</div>;
  const max = apps[0]?.secs || 1;
  return (
    <div className="app-list">
      {apps.map((a, i) => (
        <div key={i} className="aw-app-row">
          <div className="aw-app-header">
            <span className="aw-rank">{i + 1}</span>
            <span className="aw-name">{a.app}</span>
            <span className="aw-time">{fmtSecs(a.secs)}</span>
          </div>
          <div className="aw-bar-track">
            <div className="aw-bar-fill" style={{ width: `${(a.secs / max) * 100}%`, background: color }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function EmployeeCard({ e }) {
  const [tab, setTab] = useState("work");
  const statusColor = { Online: "#10b981", Idle: "#f59e0b", Offline: "#6b7280" }[e.status] || "#6b7280";
  const diskPct = e.disk?.pctUsed || 0;
  const diskColor = diskPct >= 90 ? "#ef4444" : diskPct >= 75 ? "#f97316" : "#6c8cff";
  const hasActivity = (e.totalSecs || 0) > 0;

  return (
    <div className="emp-card" style={{ "--status-color": statusColor }}>

      {/* ── Header ── */}
      <div className="card-header">
        <div className="avatar" style={{ background: `linear-gradient(135deg,${statusColor}33,${statusColor}11)`, borderColor: statusColor + "44" }}>
          {initials(e.username)}
        </div>
        <div className="card-title">
          <div className="emp-name">{e.username}</div>
          <div className="emp-sub">💻 {e.computer} &nbsp;·&nbsp; 📍 {e.location || "Unknown"}</div>
          <div className="emp-sub">🌐 {e.ip || "—"} &nbsp;·&nbsp; S/N: {e.serial || "N/A"}</div>
        </div>
        <div className="card-status">
          <StatusDot status={e.status} />
          {e.vpn && <div className="vpn-pill">🔒 {e.vpn}</div>}
          {e.remoteApp && <div className="vpn-pill remote-pill">🖥️ {e.remoteApp}</div>}
        </div>
      </div>

      {/* ── Time Strip ── */}
      <div className="time-row">
        <div className="time-info highlight">
          <span className="ti-icon">🟢</span>
          <div>
            <div className="ti-label">Today Login</div>
            <div className="ti-val">{e.todayLogin || "—"}</div>
          </div>
        </div>
        <div className="time-info">
          <span className="ti-icon">🔴</span>
          <div>
            <div className="ti-label">Shutdown</div>
            <div className="ti-val">{e.todayShutdown || "—"}</div>
          </div>
        </div>
        <div className="time-info">
          <span className="ti-icon">🔵</span>
          <div>
            <div className="ti-label">Prev Day Login</div>
            <div className="ti-val">{e.prevDayLogin || "—"}</div>
          </div>
        </div>
        <div className="time-info">
          <span className="ti-icon">⏱</span>
          <div>
            <div className="ti-label">Last Seen</div>
            <div className="ti-val">{e.lastEvent || "—"}</div>
          </div>
        </div>
      </div>

      {/* ── Activity Bar (ActivityWatch style) ── */}
      <div className="section-block">
        <div className="sec-title-row">
          <span className="sec-title">Today's Activity</span>
          {hasActivity && <span className="total-time">Total: {fmtSecs(e.totalSecs)}</span>}
        </div>
        {hasActivity
          ? <ActivityBar workPct={e.workPct || 0} commsPct={e.commsPct || 0} nonworkPct={e.nonworkPct || 0} />
          : <div className="no-data">No activity recorded today</div>
        }
      </div>

      {/* ── App Detail Tabs ── */}
      <div className="detail-tabs">
        <button className={`dtab ${tab === "work" ? "active work-active" : ""}`} onClick={() => setTab("work")}>
          💼 Work Details
        </button>
        <button className={`dtab ${tab === "comms" ? "active comms-active" : ""}`} onClick={() => setTab("comms")}>
          💬 Comms Details
        </button>
        <button className={`dtab ${tab === "top" ? "active top-active" : ""}`} onClick={() => setTab("top")}>
          📊 Top Apps
        </button>
        <button className={`dtab ${tab === "browser" ? "active browser-active" : ""}`} onClick={() => setTab("browser")}>
          🌐 Browser
        </button>
        <button className={`dtab ${tab === "disk" ? "active disk-active" : ""}`} onClick={() => setTab("disk")}>
          💾 Storage
        </button>
      </div>

      <div className="detail-body">
        {tab === "browser" && (
          <div className="browser-sites">
            {e.topSites && e.topSites.length > 0 ? (
              <>
                <div className="browser-header">Top websites visited today (Chrome / Edge)</div>
                {e.topSites.map((s, i) => {
                  const max = e.topSites[0]?.secs || 1;
                  const pct = Math.round((s.secs / max) * 100);
                  const isWork = /apollo|salesforce|hubspot|linkedin|zoho|pipedrive|notion|jira|confluence|github|gitlab|office|outlook|teams|docs\.google|sheets\.google|drive\.google|meet\.google|zoom|webex|monday|asana|trello|slack/.test(s.domain);
                  const isSocial = /youtube|instagram|facebook|twitter|tiktok|snapchat|reddit|netflix|hotstar|spotify|discord|whatsapp|telegram/.test(s.domain);
                  const barColor = isSocial ? "linear-gradient(90deg,#ef4444,#f87171)" : isWork ? "linear-gradient(90deg,#6c8cff,#818cf8)" : "linear-gradient(90deg,#f59e0b,#fbbf24)";
                  return (
                    <div key={i} className="aw-app-row">
                      <div className="aw-app-header">
                        <span className="aw-rank">{i + 1}</span>
                        <span className="aw-name" style={{ display:"flex", alignItems:"center", gap:6 }}>
                          <img src={`https://www.google.com/s2/favicons?domain=${s.domain}&sz=16`} width={16} height={16} style={{ borderRadius:3 }} onError={(ev)=>ev.target.style.display="none"} />
                          {s.domain}
                          {isSocial && <span style={{ fontSize:"0.65rem", background:"rgba(239,68,68,.15)", color:"#ef4444", borderRadius:4, padding:"1px 5px" }}>⚠ Non-work</span>}
                          {isWork && <span style={{ fontSize:"0.65rem", background:"rgba(108,140,255,.15)", color:"#6c8cff", borderRadius:4, padding:"1px 5px" }}>✓ Work</span>}
                        </span>
                        <span className="aw-time">{fmtSecs(s.secs)}</span>
                      </div>
                      <div className="aw-bar-track">
                        <div className="aw-bar-fill" style={{ width: `${pct}%`, background: barColor }} />
                      </div>
                    </div>
                  );
                })}
              </>
            ) : <div className="no-data">No browser data yet — will appear on next heartbeat (every 5 min)</div>}
          </div>
        )}
        {tab === "work" && (
          <>
            <div className="detail-pct-row">
              <div className="detail-pct-circle" style={{ background: "conic-gradient(#6c8cff " + (e.workPct||0)*3.6 + "deg, var(--bg3) 0deg)" }}>
                <div className="detail-pct-inner">
                  <span className="detail-pct-num">{e.workPct||0}%</span>
                  <span className="detail-pct-lbl">Work</span>
                </div>
              </div>
              <div className="detail-pct-info">
                <div className="detail-pct-time">{fmtSecs(e.totalSecs ? Math.round(e.totalSecs*(e.workPct||0)/100) : 0)}</div>
                <div className="detail-pct-sub">of {fmtSecs(e.totalSecs)} total today</div>
                <div className="detail-pct-badge" style={{ background:"rgba(108,140,255,.12)", color:"#6c8cff", border:"1px solid rgba(108,140,255,.3)" }}>💼 Work Activity</div>
              </div>
            </div>
            <AppList apps={e.workApps} color="linear-gradient(90deg,#6c8cff,#818cf8)" emptyMsg="No work app activity today" />
          </>
        )}
        {tab === "comms" && (
          <>
            <div className="detail-pct-row">
              <div className="detail-pct-circle" style={{ background: "conic-gradient(#10b981 " + (e.commsPct||0)*3.6 + "deg, var(--bg3) 0deg)" }}>
                <div className="detail-pct-inner">
                  <span className="detail-pct-num">{e.commsPct||0}%</span>
                  <span className="detail-pct-lbl">Comms</span>
                </div>
              </div>
              <div className="detail-pct-info">
                <div className="detail-pct-time">{fmtSecs(e.totalSecs ? Math.round(e.totalSecs*(e.commsPct||0)/100) : 0)}</div>
                <div className="detail-pct-sub">of {fmtSecs(e.totalSecs)} total today</div>
                <div className="detail-pct-badge" style={{ background:"rgba(16,185,129,.12)", color:"#10b981", border:"1px solid rgba(16,185,129,.3)" }}>💬 Communications</div>
              </div>
            </div>
            <AppList apps={e.commsApps} color="linear-gradient(90deg,#10b981,#34d399)" emptyMsg="No communication app activity today" />
          </>
        )}
        {tab === "top" && (
          <AppList apps={e.topApps} color="linear-gradient(90deg,#a855f7,#c084fc)" emptyMsg="No app data today" />
        )}
        {tab === "disk" && (
          <div className="disk-section">
            {e.disk ? (
              <>
                <div className="disk-nums">
                  <span>{e.disk.usedGb} GB used</span>
                  <span style={{ color: diskColor, fontWeight: 700 }}>{diskPct}%</span>
                  <span>{e.disk.totalGb} GB total</span>
                </div>
                <div className="disk-track">
                  <div className="disk-fill" style={{ width: `${Math.min(diskPct,100)}%`, background: diskColor }} />
                </div>
                <div className="disk-free">{e.disk.freeGb} GB free</div>
              </>
            ) : <div className="no-data">No disk data</div>}
          </div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const [employees, setEmployees] = useState([]);
  const [connected, setConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [theme, setTheme] = useState(() => localStorage.getItem("empmon_theme") || "light");
  const [tab, setTab] = useState("live");
  const [search, setSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState("All");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("empmon_theme", theme);
  }, [theme]);

  useEffect(() => {
    const socket = io(SOCKET_URL);
    socket.on("connect", () => setConnected(true));
    socket.on("disconnect", () => setConnected(false));
    socket.on("employees:update", (data) => {
      setEmployees(data);
      setLastUpdate(new Date());
    });
    return () => socket.disconnect();
  }, []);

  const onlineCount  = employees.filter((e) => e.status === "Online").length;
  const idleCount    = employees.filter((e) => e.status === "Idle").length;
  const offlineCount = employees.filter((e) => e.status === "Offline").length;

  const filtered = employees.filter((e) => {
    const matchSearch = !search ||
      e.username.toLowerCase().includes(search.toLowerCase()) ||
      e.computer.toLowerCase().includes(search.toLowerCase());
    const matchStatus = filterStatus === "All" || e.status === filterStatus;
    return matchSearch && matchStatus;
  });

  return (
    <div className="dashboard">
      <header className="navbar">
        <div className="navbar-brand">
          <div className="brand-icon">W</div>
          <div>
            <div className="brand-name">W-SAFE REINSURANCE</div>
            <div className="brand-sub">Employee Activity Monitor — Real-Time</div>
          </div>
        </div>
        <div className="navbar-center">
          <button className={`nav-tab ${tab === "live" ? "active" : ""}`} onClick={() => setTab("live")}>⚡ Live Dashboard</button>
          <button className={`nav-tab ${tab === "monthly" ? "active" : ""}`} onClick={() => setTab("monthly")}>📊 Monthly Report</button>
        </div>
        <div className="navbar-right">
          <div className={`live-indicator ${connected ? "live" : "dead"}`}>
            <span className="live-dot" />
            {connected ? "LIVE" : "OFFLINE"}
          </div>
          {lastUpdate && <span className="update-time">{lastUpdate.toLocaleTimeString()}</span>}
          <button className="theme-btn" onClick={() => setTheme((t) => (t === "light" ? "dark" : "light"))} title="Toggle theme">
            {theme === "light" ? "🌙" : "☀️"}
          </button>
        </div>
      </header>

      {tab === "monthly" && <MonthlyReport />}

      {tab === "live" && (
        <div className="live-view">
          <div className="stats-strip">
            <div className="stat-box total">
              <div className="sb-icon">👥</div>
              <div><div className="sb-num">{employees.length}</div><div className="sb-lbl">Total</div></div>
            </div>
            <div className="stat-box online">
              <div className="sb-icon">🟢</div>
              <div><div className="sb-num">{onlineCount}</div><div className="sb-lbl">Online</div></div>
            </div>
            <div className="stat-box idle">
              <div className="sb-icon">🟡</div>
              <div><div className="sb-num">{idleCount}</div><div className="sb-lbl">Idle</div></div>
            </div>
            <div className="stat-box offline">
              <div className="sb-icon">🔴</div>
              <div><div className="sb-num">{offlineCount}</div><div className="sb-lbl">Offline</div></div>
            </div>
          </div>

          <div className="filter-bar">
            <input className="search-box" placeholder="🔍  Search employee or computer..."
              value={search} onChange={(e) => setSearch(e.target.value)} />
            <div className="filter-pills">
              {["All","Online","Idle","Offline"].map((s) => (
                <button key={s} className={`filter-pill ${filterStatus === s ? "active" : ""}`}
                  onClick={() => setFilterStatus(s)}>{s}</button>
              ))}
            </div>
          </div>

          <div className="cards-grid">
            {filtered.map((e) => (
              <EmployeeCard key={`${e.username}-${e.computer}`} e={e} />
            ))}
            {!filtered.length && (
              <div className="empty-state">
                <div className="empty-icon">📡</div>
                <div className="empty-title">Waiting for employees...</div>
                <div className="empty-sub">Make sure employee agents are running and connected.</div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
