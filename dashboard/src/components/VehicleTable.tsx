import type { Vehicle } from "../types";

interface Props {
  vehicles: Vehicle[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  loading: boolean;
}

const FUEL_ICONS: Record<string, string> = {
  petrol: "⛽",
  diesel: "🛢",
  electric: "⚡",
  cng: "💨",
  hybrid: "🔋",
};

const GROUP_COLORS: Record<string, string> = {
  executive: "#C49A2A",
  delivery: "#6398E8",
  field_ops: "#E5A33D",
  logistics: "#36B37E",
};

function FuelBar({ value }: { value: number | null }) {
  const pct = value ?? 0;
  const color = pct < 15 ? "#D63B2F" : pct < 30 ? "#E5A33D" : "#36B37E";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div
        style={{
          flex: 1,
          height: 4,
          background: "#30353E",
          borderRadius: 2,
          overflow: "hidden",
        }}
      >
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 2, transition: "width 0.4s" }} />
      </div>
      <span style={{ fontSize: 11, color: "#A1A7B1", minWidth: 28, textAlign: "right" }}>
        {value !== null ? `${Math.round(value)}%` : "—"}
      </span>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { bg: string; color: string; label: string }> = {
    active: { bg: "rgba(54,179,126,0.12)", color: "#36B37E", label: "Active" },
    idle: { bg: "rgba(161,167,177,0.12)", color: "#A1A7B1", label: "Idle" },
    maintenance: { bg: "rgba(229,163,61,0.12)", color: "#E5A33D", label: "Maintenance" },
    offline: { bg: "rgba(214,59,47,0.12)", color: "#D63B2F", label: "Offline" },
  };
  const s = map[status] ?? { bg: "rgba(161,167,177,0.12)", color: "#A1A7B1", label: status };
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.07em",
        padding: "2px 7px",
        borderRadius: 4,
        background: s.bg,
        color: s.color,
      }}
    >
      {s.label}
    </span>
  );
}

export function VehicleTable({ vehicles, selectedId, onSelect, loading }: Props) {
  return (
    <div
      style={{
        background: "#191D23",
        border: "1px solid #30353E",
        borderRadius: 12,
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "14px 18px",
          borderBottom: "1px solid #30353E",
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: "#F4F1E8" }}>Fleet Vehicles</span>
        <span style={{ fontSize: 11, color: "#A1A7B1" }}>{vehicles.length} records</span>
      </div>

      {/* Table header */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "2fr 1fr 1fr 1fr 1.4fr 1fr",
          padding: "8px 18px",
          borderBottom: "1px solid #22262D",
          gap: 8,
        }}
      >
        {["Vehicle", "Group", "Status", "Speed", "Fuel", "Odometer"].map((h) => (
          <span key={h} style={{ fontSize: 10, color: "#A1A7B1", textTransform: "uppercase", letterSpacing: "0.07em", fontWeight: 600 }}>
            {h}
          </span>
        ))}
      </div>

      {/* Rows */}
      {loading ? (
        <div style={{ padding: "32px 18px", textAlign: "center", color: "#A1A7B1", fontSize: 13 }}>
          Loading vehicles…
        </div>
      ) : vehicles.length === 0 ? (
        <div style={{ padding: "32px 18px", textAlign: "center", color: "#A1A7B1", fontSize: 13 }}>
          No vehicles found
        </div>
      ) : (
        vehicles.map((v) => {
          const selected = v.id === selectedId;
          return (
            <div
              key={v.id}
              id={`vehicle-row-${v.id}`}
              onClick={() => onSelect(v.id)}
              style={{
                display: "grid",
                gridTemplateColumns: "2fr 1fr 1fr 1fr 1.4fr 1fr",
                padding: "10px 18px",
                gap: 8,
                alignItems: "center",
                cursor: "pointer",
                background: selected ? "#22262D" : "transparent",
                borderLeft: selected ? "2px solid #C49A2A" : "2px solid transparent",
                borderBottom: "1px solid #22262D",
                transition: "background 0.12s",
              }}
              onMouseEnter={(e) => {
                if (!selected) (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.03)";
              }}
              onMouseLeave={(e) => {
                if (!selected) (e.currentTarget as HTMLElement).style.background = "transparent";
              }}
            >
              {/* Vehicle name */}
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, color: "#F4F1E8" }}>
                  {FUEL_ICONS[v.fuel_type] ?? "🚗"} {v.make} {v.model}
                </div>
                <div style={{ fontSize: 11, color: "#A1A7B1", marginTop: 2 }}>
                  {v.license_plate} · {v.year}
                </div>
              </div>

              {/* Group */}
              <span
                style={{
                  fontSize: 11,
                  color: GROUP_COLORS[v.fleet_group] ?? "#A1A7B1",
                  fontWeight: 500,
                  textTransform: "capitalize",
                }}
              >
                {v.fleet_group?.replace("_", " ") ?? "—"}
              </span>

              {/* Status */}
              <StatusBadge status={v.status} />

              {/* Speed */}
              <span style={{ fontSize: 13, fontVariantNumeric: "tabular-nums", color: "#F4F1E8" }}>
                {v.current_speed !== null ? `${v.current_speed.toFixed(0)} km/h` : "—"}
              </span>

              {/* Fuel */}
              <FuelBar value={v.current_fuel_level} />

              {/* Odometer */}
              <span style={{ fontSize: 11, color: "#A1A7B1", fontVariantNumeric: "tabular-nums" }}>
                {v.odometer !== null ? `${v.odometer.toLocaleString()} km` : "—"}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
}
