import type { FleetStatus } from "../types";

interface Props {
  status: FleetStatus | null;
}

function StatCard({
  id,
  label,
  value,
  sub,
  accent,
}: {
  id: string;
  label: string;
  value: string | number;
  sub?: string;
  accent?: string;
}) {
  return (
    <div
      id={id}
      style={{
        background: "#191D23",
        border: "1px solid #30353E",
        borderRadius: 12,
        padding: "18px 20px",
        minWidth: 0,
      }}
    >
      <div style={{ fontSize: 11, color: "#A1A7B1", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 28, fontWeight: 700, color: accent ?? "#F4F1E8", fontVariantNumeric: "tabular-nums" }}>
        {value === null || value === undefined ? "—" : value}
      </div>
      {sub && <div style={{ fontSize: 12, color: "#A1A7B1", marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

export function SummaryCards({ status }: Props) {
  const v = status?.vehicles;
  const a = status?.alerts_24h;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
        gap: 12,
      }}
    >
      <StatCard
        id="card-total-vehicles"
        label="Total Vehicles"
        value={v?.total ?? "—"}
        sub={v ? `${v.active} active · ${v.idle} idle` : undefined}
      />
      <StatCard
        id="card-active-vehicles"
        label="Active"
        value={v?.active ?? "—"}
        accent="#36B37E"
        sub="Currently running"
      />
      <StatCard
        id="card-alerts-24h"
        label="Alerts (24h)"
        value={a?.total ?? "—"}
        sub={a ? `${a.unacknowledged} unacknowledged` : undefined}
        accent={a && a.total > 0 ? "#E5A33D" : undefined}
      />
      <StatCard
        id="card-critical-alerts"
        label="Critical"
        value={a?.critical ?? "—"}
        accent={a && a.critical > 0 ? "#D63B2F" : undefined}
        sub="Last 24 hours"
      />
      <StatCard
        id="card-warnings"
        label="Warnings"
        value={a?.warning ?? "—"}
        accent={a && a.warning > 0 ? "#E5A33D" : undefined}
        sub="Last 24 hours"
      />
    </div>
  );
}
