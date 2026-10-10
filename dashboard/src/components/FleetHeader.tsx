import type { FleetStatus } from "../types";

interface Props {
  status: FleetStatus | null;
  lastUpdated: Date | null;
  unreadAlerts: number;
  onNotificationsClick: () => void;
}

function fmt(d: Date) {
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function FleetHeader({ status, lastUpdated, unreadAlerts, onNotificationsClick }: Props) {
  const online = status?.status === "online";

  return (
    <header
      style={{
        background: "#12151A",
        borderBottom: "1px solid #30353E",
        padding: "0 24px",
        height: 56,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        position: "sticky",
        top: 0,
        zIndex: 100,
      }}
    >
      {/* Left: brand */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: 8,
            background: "#1E2229",
            border: "1px solid #30353E",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 16,
          }}
        >
          🚛
        </div>
        <div>
          <div style={{ fontSize: 15, fontWeight: 600, color: "#F4F1E8", letterSpacing: "-0.2px" }}>
            Fleet Monitor
          </div>
          <div style={{ fontSize: 11, color: "#A1A7B1", marginTop: 1 }}>
            {status?.system_name ?? "Fleet Management System"}
          </div>
        </div>
      </div>

      {/* Right: status chips */}
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        {/* Connection dot */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: online ? "#36B37E" : "#D63B2F",
              display: "inline-block",
              boxShadow: online ? "0 0 0 2px rgba(54,179,126,0.2)" : "none",
            }}
          />
          <span style={{ fontSize: 12, color: "#A1A7B1" }}>{online ? "Connected" : "Offline"}</span>
        </div>

        {/* Simulator badge */}
        {status && (
          <div
            style={{
              fontSize: 11,
              fontWeight: 500,
              padding: "3px 8px",
              borderRadius: 6,
              border: "1px solid",
              borderColor: status.simulation_running ? "#C49A2A" : "#30353E",
              color: status.simulation_running ? "#C49A2A" : "#A1A7B1",
              background: status.simulation_running ? "rgba(196,154,42,0.08)" : "transparent",
            }}
          >
            {status.simulation_running ? "● SIM LIVE" : "○ SIM PAUSED"}
          </div>
        )}

        {/* Last updated */}
        {lastUpdated && (
          <span style={{ fontSize: 11, color: "#A1A7B1" }}>Updated {fmt(lastUpdated)}</span>
        )}

        {/* Notification bell */}
        <button
          id="fleet-notifications-btn"
          onClick={onNotificationsClick}
          style={{
            position: "relative",
            background: "transparent",
            border: "1px solid #30353E",
            borderRadius: 8,
            width: 34,
            height: 34,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#A1A7B1",
            fontSize: 14,
            transition: "border-color 0.15s",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.borderColor = "#C49A2A")}
          onMouseLeave={(e) => (e.currentTarget.style.borderColor = "#30353E")}
        >
          🔔
          {unreadAlerts > 0 && (
            <span
              style={{
                position: "absolute",
                top: -4,
                right: -4,
                background: "#D63B2F",
                color: "#fff",
                fontSize: 9,
                fontWeight: 700,
                width: 16,
                height: 16,
                borderRadius: "50%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {unreadAlerts > 9 ? "9+" : unreadAlerts}
            </span>
          )}
        </button>
      </div>
    </header>
  );
}
