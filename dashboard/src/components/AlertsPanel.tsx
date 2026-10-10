import { useState } from "react";
import type { Alert } from "../types";
import { fleetApi } from "../api";

interface Props {
  alerts: Alert[];
  loading: boolean;
  onRefresh: () => void;
}

const SEV: Record<string, { bg: string; border: string; color: string; dot: string; label: string }> = {
  critical: {
    bg: "rgba(214,59,47,0.07)",
    border: "#D63B2F",
    color: "#D63B2F",
    dot: "#D63B2F",
    label: "Critical",
  },
  warning: {
    bg: "rgba(229,163,61,0.07)",
    border: "#E5A33D",
    color: "#E5A33D",
    dot: "#E5A33D",
    label: "Warning",
  },
  info: {
    bg: "rgba(99,152,232,0.07)",
    border: "#6398E8",
    color: "#6398E8",
    dot: "#6398E8",
    label: "Info",
  },
};

function fmtTime(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  return d.toLocaleDateString("en-IN");
}

function fmtAlertType(t: string) {
  return t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function AlertCard({ alert, onAck }: { alert: Alert; onAck: (id: number) => void }) {
  const s = SEV[alert.severity] ?? SEV.info;
  const [acking, setAcking] = useState(false);

  const handleAck = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setAcking(true);
    await fleetApi.acknowledgeAlert(alert.id);
    setAcking(false);
    onAck(alert.id);
  };

  return (
    <div
      id={`alert-card-${alert.id}`}
      style={{
        background: alert.is_acknowledged ? "#191D23" : s.bg,
        border: `1px solid ${alert.is_acknowledged ? "#30353E" : s.border}`,
        borderRadius: 10,
        padding: "12px 14px",
        opacity: alert.is_acknowledged ? 0.6 : 1,
        transition: "opacity 0.2s",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        {/* Severity dot */}
        <span
          style={{
            display: "inline-block",
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: s.dot,
            marginTop: 5,
            flexShrink: 0,
            boxShadow: alert.is_acknowledged ? "none" : `0 0 0 2px ${s.dot}33`,
          }}
        />

        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Row 1: severity badge + type */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span
              style={{
                fontSize: 9,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.09em",
                color: s.color,
                border: `1px solid ${s.border}`,
                borderRadius: 4,
                padding: "1px 5px",
              }}
            >
              {s.label}
            </span>
            <span style={{ fontSize: 12, fontWeight: 600, color: "#F4F1E8" }}>
              {fmtAlertType(alert.alert_type)}
            </span>
          </div>

          {/* Message */}
          <p style={{ fontSize: 12, color: "#A1A7B1", margin: "4px 0 0", lineHeight: 1.5 }}>
            {alert.message}
          </p>

          {/* Details */}
          {alert.details && (
            <p style={{ fontSize: 11, color: "#A1A7B1", margin: "3px 0 0", fontStyle: "italic" }}>
              {alert.details}
            </p>
          )}

          {/* Footer row */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginTop: 8,
              flexWrap: "wrap",
              gap: 4,
            }}
          >
            <div style={{ fontSize: 11, color: "#A1A7B1" }}>
              Vehicle #{alert.vehicle_id}
              {alert.trigger_value !== null && (
                <> · Value: {alert.trigger_value.toFixed(1)}</>
              )}
              {alert.threshold_value !== null && (
                <> · Threshold: {alert.threshold_value.toFixed(1)}</>
              )}
              <> · {fmtTime(alert.created_at)}</>
            </div>

            {!alert.is_acknowledged ? (
              <button
                id={`ack-btn-${alert.id}`}
                onClick={handleAck}
                disabled={acking}
                style={{
                  fontSize: 10,
                  fontWeight: 600,
                  color: "#A1A7B1",
                  background: "transparent",
                  border: "1px solid #30353E",
                  borderRadius: 5,
                  padding: "2px 8px",
                  cursor: "pointer",
                  opacity: acking ? 0.5 : 1,
                  transition: "color 0.15s, border-color 0.15s",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.color = "#F4F1E8";
                  (e.currentTarget as HTMLElement).style.borderColor = "#A1A7B1";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.color = "#A1A7B1";
                  (e.currentTarget as HTMLElement).style.borderColor = "#30353E";
                }}
              >
                {acking ? "…" : "Acknowledge"}
              </button>
            ) : (
              <span style={{ fontSize: 10, color: "#A1A7B1" }}>✓ Acknowledged</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function AlertsPanel({ alerts, loading, onRefresh }: Props) {
  const [filter, setFilter] = useState<"all" | "critical" | "warning" | "info">("all");
  const [unackOnly, setUnackOnly] = useState(false);

  const filtered = alerts.filter((a) => {
    if (filter !== "all" && a.severity !== filter) return false;
    if (unackOnly && a.is_acknowledged) return false;
    return true;
  });

  const handleAck = () => onRefresh();

  return (
    <div
      style={{
        background: "#191D23",
        border: "1px solid #30353E",
        borderRadius: 12,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        maxHeight: "calc(100vh - 220px)",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "14px 16px",
          borderBottom: "1px solid #30353E",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: "#F4F1E8" }}>
          Alerts
          {filtered.length > 0 && (
            <span
              style={{
                marginLeft: 7,
                fontSize: 10,
                fontWeight: 700,
                padding: "2px 6px",
                borderRadius: 4,
                background: "rgba(214,59,47,0.15)",
                color: "#D63B2F",
              }}
            >
              {filtered.length}
            </span>
          )}
        </span>
        <button
          onClick={onRefresh}
          style={{
            fontSize: 12,
            background: "transparent",
            border: "none",
            color: "#A1A7B1",
            cursor: "pointer",
            padding: "2px 4px",
          }}
        >
          ↻
        </button>
      </div>

      {/* Filters */}
      <div
        style={{
          padding: "10px 16px",
          borderBottom: "1px solid #22262D",
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
          alignItems: "center",
          flexShrink: 0,
        }}
      >
        {(["all", "critical", "warning", "info"] as const).map((f) => (
          <button
            key={f}
            id={`alert-filter-${f}`}
            onClick={() => setFilter(f)}
            style={{
              fontSize: 11,
              fontWeight: 500,
              padding: "3px 9px",
              borderRadius: 6,
              cursor: "pointer",
              border: "1px solid",
              borderColor: filter === f ? "#C49A2A" : "#30353E",
              background: filter === f ? "rgba(196,154,42,0.1)" : "transparent",
              color: filter === f ? "#C49A2A" : "#A1A7B1",
              textTransform: "capitalize",
              transition: "all 0.12s",
            }}
          >
            {f}
          </button>
        ))}
        <label
          style={{
            fontSize: 11,
            color: "#A1A7B1",
            display: "flex",
            alignItems: "center",
            gap: 4,
            cursor: "pointer",
            marginLeft: "auto",
          }}
        >
          <input
            type="checkbox"
            checked={unackOnly}
            onChange={(e) => setUnackOnly(e.target.checked)}
            style={{ accentColor: "#C49A2A" }}
          />
          Unack only
        </label>
      </div>

      {/* List */}
      <div style={{ overflow: "auto", flex: 1, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
        {loading ? (
          <div style={{ padding: "24px 0", textAlign: "center", color: "#A1A7B1", fontSize: 13 }}>
            Loading alerts…
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: "24px 0", textAlign: "center", color: "#A1A7B1", fontSize: 13 }}>
            No alerts matching filter
          </div>
        ) : (
          filtered.map((a) => <AlertCard key={a.id} alert={a} onAck={handleAck} />)
        )}
      </div>
    </div>
  );
}
