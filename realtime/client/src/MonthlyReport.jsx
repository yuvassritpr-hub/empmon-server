import { useEffect, useState } from "react";

const API_URL = "https://empmon-server.onrender.com";

function fmt(s) {
  if (!s || s === 0) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m`;
}

function RiskBadge({ risk }) {
  const map = {
    HIGH:   { bg: "rgba(239,68,68,.12)",  border: "rgba(239,68,68,.4)",  color: "#ef4444" },
    MEDIUM: { bg: "rgba(245,158,11,.12)", border: "rgba(245,158,11,.4)", color: "#f59e0b" },
    LOW:    { bg: "rgba(16,185,129,.12)", border: "rgba(16,185,129,.4)", color: "#10b981" },
  };
  const s = map[risk] || map.LOW;
  return (
    <span style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.color,
      padding: "2px 9px", borderRadius: 10, fontSize: ".68rem", fontWeight: 700 }}>
      {risk}
    </span>
  );
}

function SectionTitle({ children }) {
  return <div className="section-hdr">{children}</div>;
}

function AlertList({ items, keyField, label, emptyMsg }) {
  if (!items?.length) return <div className="no-alert">✅ {emptyMsg}</div>;
  return items.map((item, i) => (
    <div key={i} className="alert-row">
      <span className="plat">{item[keyField]}</span>
      <span className="dur">{item.dur}</span>
      <RiskBadge risk={item.risk} />
    </div>
  ));
}

function ActivityBar({ workPct, commsPct, nonworkPct }) {
  return (
    <>
      <div className="bar-wrap" style={{ borderRadius: 8, overflow: "hidden" }}>
        <div style={{ width: `${workPct}%`, background: "linear-gradient(90deg,#6c8cff,#818cf8)", transition: "width .4s" }} />
        <div style={{ width: `${commsPct}%`, background: "linear-gradient(90deg,#10b981,#34d399)", transition: "width .4s" }} />
        <div style={{ width: `${nonworkPct}%`, background: "linear-gradient(90deg,#f59e0b,#fbbf24)", transition: "width .4s" }} />
      </div>
      <div style={{ display: "flex", gap: 16, fontSize: ".76rem", color: "var(--text-dim)", marginTop: 6, flexWrap: "wrap" }}>
        <span><span style={{ display:"inline-block",width:8,height:8,borderRadius:"50%",background:"#6c8cff",marginRight:5 }}/>Work <strong style={{color:"var(--text)"}}>{workPct}%</strong></span>
        <span><span style={{ display:"inline-block",width:8,height:8,borderRadius:"50%",background:"#10b981",marginRight:5 }}/>Comms <strong style={{color:"var(--text)"}}>{commsPct}%</strong></span>
        <span><span style={{ display:"inline-block",width:8,height:8,borderRadius:"50%",background:"#f59e0b",marginRight:5 }}/>Other <strong style={{color:"var(--text)"}}>{nonworkPct}%</strong></span>
      </div>
    </>
  );
}

function DailyTable({ rows }) {
  if (!rows?.length) return <div className="no-alert">No daily data</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="daily-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Login</th>
            <th>Shutdown</th>
            <th>Work Hrs</th>
            <th>Comms</th>
            <th>Idle Time</th>
            <th>🔒 Locks</th>
            <th>🔓 Unlocks</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d, i) => (
            <tr key={i}>
              <td className="day-date">{d.date}</td>
              <td className="day-login">{d.login !== "--" ? d.login : <span className="dash">—</span>}</td>
              <td className="day-shutdown">{d.shutdown !== "--" ? d.shutdown : <span className="dash">—</span>}</td>
              <td className="day-work">
                {d.workSecs > 0
                  ? <span className="time-chip work-chip">{fmt(d.workSecs)}</span>
                  : <span className="dash">—</span>}
              </td>
              <td className="day-comms">
                {d.commsSecs > 0
                  ? <span className="time-chip comms-chip">{fmt(d.commsSecs)}</span>
                  : <span className="dash">—</span>}
              </td>
              <td className="day-idle">
                {d.idleSecs > 300
                  ? <span className="time-chip idle-chip">{fmt(d.idleSecs)}</span>
                  : <span className="dash">—</span>}
              </td>
              <td className="day-lock">
                {d.lockCount > 0
                  ? <span className="count-badge lock-badge" title={d.lockTimes}>{d.lockCount}×</span>
                  : <span className="dash">—</span>}
              </td>
              <td className="day-unlock">
                {d.unlockCount > 0
                  ? <span className="count-badge unlock-badge" title={d.unlockTimes}>{d.unlockCount}×</span>
                  : <span className="dash">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MonthlyReport() {
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedEmp, setExpandedEmp] = useState(null);

  useEffect(() => {
    setLoading(true);
    fetch(`${API_URL}/api/monthly/${month}`)
      .then((r) => r.json())
      .then((data) => { setEmployees(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, [month]);

  const months = Array.from({ length: 6 }, (_, i) => {
    const d = new Date();
    d.setMonth(d.getMonth() - i);
    return { val: d.toISOString().slice(0, 7), label: d.toLocaleString("default", { month: "short", year: "numeric" }) };
  });

  return (
    <div className="monthly-wrap" style={{ padding: "0 0 40px" }}>
      {/* Month selector */}
      <div className="month-nav">
        <span style={{ color: "var(--text-dim)", fontSize: ".82rem", fontWeight: 600 }}>📅 Month:</span>
        {months.map((m) => (
          <button key={m.val} className={`month-btn ${m.val === month ? "active" : ""}`} onClick={() => setMonth(m.val)}>
            {m.label}
          </button>
        ))}
      </div>

      {loading && (
        <div style={{ textAlign: "center", padding: 60, color: "var(--text-dim)" }}>
          <div style={{ fontSize: "2rem", marginBottom: 12 }}>⏳</div>
          Loading report...
        </div>
      )}
      {!loading && !employees.length && (
        <div style={{ textAlign: "center", padding: 60, color: "var(--text-dim)" }}>
          <div style={{ fontSize: "2rem", marginBottom: 12 }}>📭</div>
          No data found for this month
        </div>
      )}

      {employees.map((e) => {
        const totalActiveDays = e.dailyBreakdown?.filter(d => d.login !== "--").length || 0;
        const totalWorkSecs = e.dailyBreakdown?.reduce((a, d) => a + (d.workSecs || 0), 0) || 0;
        const totalIdleSecs = e.dailyBreakdown?.reduce((a, d) => a + (d.idleSecs || 0), 0) || 0;
        const isExpanded = expandedEmp === `${e.username}-${e.computer}`;

        return (
          <div className="monthly-emp-card" key={`${e.username}-${e.computer}`}>

            {/* Employee Header */}
            <div className="monthly-emp-header">
              <div className="me-avatar">{(e.username || "?").slice(0, 2).toUpperCase()}</div>
              <div className="me-info">
                <div className="me-name">{e.username}</div>
                <div className="me-meta">
                  💻 {e.computer} &nbsp;·&nbsp; 🔢 {e.serial} &nbsp;·&nbsp; 📍 {e.location} &nbsp;·&nbsp; 🌐 {e.ip}
                </div>
              </div>
              <div className="me-stats">
                <div className="me-stat-box">
                  <div className="me-stat-val">{fmt(totalWorkSecs) || "—"}</div>
                  <div className="me-stat-lbl">Work Time</div>
                </div>
                <div className="me-stat-box">
                  <div className="me-stat-val" style={{ color: "#f59e0b" }}>{fmt(totalIdleSecs) || "—"}</div>
                  <div className="me-stat-lbl">Idle Time</div>
                </div>
                <div className="me-stat-box">
                  <div className="me-stat-val" style={{ color: "#6c8cff" }}>{totalActiveDays}</div>
                  <div className="me-stat-lbl">Active Days</div>
                </div>
                <div className="me-stat-box">
                  <div className="me-stat-val">{e.activeHrs}</div>
                  <div className="me-stat-lbl">Total Active</div>
                </div>
              </div>
            </div>

            {/* Activity Bar */}
            <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--card-border)" }}>
              <ActivityBar workPct={e.workPct || 0} commsPct={e.commsPct || 0} nonworkPct={e.nonworkPct || 0} />
            </div>

            {/* Daily Attendance Table — always visible */}
            <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--card-border)" }}>
              <SectionTitle>📅 Daily Login / Shutdown / Work Hours / Idle / Lock &amp; Unlock</SectionTitle>
              <DailyTable rows={e.dailyBreakdown} />
            </div>

            {/* Expandable details */}
            <div
              className="expand-toggle"
              onClick={() => setExpandedEmp(isExpanded ? null : `${e.username}-${e.computer}`)}
            >
              {isExpanded ? "▲ Hide Details" : "▼ Show App Details & Alerts"}
            </div>

            {isExpanded && (
              <div className="emp-body">
                <div className="grid-3">
                  <div>
                    <SectionTitle>💼 Top Applications</SectionTitle>
                    {e.topApps?.slice(0, 8).map((a, i) => (
                      <div key={i} className="app-chip">{i + 1}. {a.app} <span className="muted" style={{ float: "right" }}>{a.dur}</span></div>
                    ))}
                    {!e.topApps?.length && <span className="no-alert">No app data</span>}
                  </div>

                  <div>
                    <SectionTitle>🚨 Social Media Alert</SectionTitle>
                    <AlertList items={e.socialAlerts} keyField="platform" emptyMsg="No social media detected" />
                  </div>

                  <div>
                    <SectionTitle>📧 External Email Alert</SectionTitle>
                    <AlertList items={e.externalEmailAlerts} keyField="provider" emptyMsg="No personal email usage" />
                  </div>

                  <div>
                    <SectionTitle>📤 File Sharing Alert</SectionTitle>
                    <AlertList items={e.fileshareAlerts} keyField="platform" emptyMsg="No file sharing detected" />
                  </div>

                  <div>
                    <SectionTitle>🔌 USB / Pendrive Alert</SectionTitle>
                    {e.usbAlerts?.length ? e.usbAlerts.map((u, i) => (
                      <div key={i} className="alert-row">
                        <span className="plat">{u.label} ({u.sizeGb}GB)</span>
                        <span className="dur">{u.date} {u.time}</span>
                        <RiskBadge risk="HIGH" />
                      </div>
                    )) : <span className="no-alert">✅ No USB drives detected</span>}
                  </div>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
