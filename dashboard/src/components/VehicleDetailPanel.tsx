import { useState } from "react";
import type { VehicleDetail } from "../types";
import { fleetApi } from "../api";

interface Props {
  detail: VehicleDetail | null;
  loading: boolean;
  onClose: () => void;
}

const ANOMALIES = [
  { value: "speeding", label: "Overspeed (158 km/h)", severity: "critical" },
  { value: "overheating", label: "Engine Overheating (119 °C)", severity: "critical" },
  { value: "low_fuel", label: "Low Fuel (4%)", severity: "critical" },
  { value: "harsh_braking", label: "Harsh Braking", severity: "warning" },
  { value: "dtc_misfire", label: "DTC Misfire Code", severity: "warning" },
  { value: "high_rpm", label: "High RPM (6800)", severity: "warning" },
];

export function VehicleDetailPanel({ detail, loading, onClose }: Props) {
  const [injecting, setInjecting] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  const v = detail?.vehicle;

  const handleInject = async (anomalyType: string) => {
    if (!v) return;
    setInjecting(anomalyType);
    setLastResult(null);
    const result = await fleetApi.triggerAnomaly(v.id, anomalyType);
    if (result) {
      setLastResult(`${result.alerts_triggered} alert(s) triggered`);
    } else {
      setLastResult("Injection failed");
    }
    setInjecting(null);
  };

  return (
    <div
      style={{
        background: "#191D23",
        border: "1px solid #30353E",
        borderRadius: 12,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
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
          {loading ? "Loading…" : v ? `${v.make} ${v.model}` : "Select a vehicle"}
        </span>
        {v && (
          <button
            onClick={onClose}
            style={{
              fontSize: 16,
              background: "transparent",
              border: "none",
              color: "#A1A7B1",
              cursor: "pointer",
              lineHeight: 1,
            }}
          >
            ×
          </button>
        )}
      </div>

      {!v && !loading && (
        <div
          style={{
            padding: "32px 16px",
            textAlign: "center",
            color: "#A1A7B1",
            fontSize: 13,
          }}
        >
          Click a vehicle row to see details
        </div>
      )}

      {loading && (
        <div style={{ padding: "32px 16px", textAlign: "center", color: "#A1A7B1", fontSize: 13 }}>
          Loading vehicle detail…
        </div>
      )}

      {v && !loading && (
        <div style={{ overflow: "auto", flex: 1, padding: "14px 16px", display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Vitals */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 8,
            }}
          >
            {[
              { label: "VIN", value: v.vin },
              { label: "Plate", value: v.license_plate },
              { label: "Speed", value: v.current_speed !== null ? `${v.current_speed.toFixed(0)} km/h` : "—" },
              { label: "Fuel", value: v.current_fuel_level !== null ? `${v.current_fuel_level.toFixed(0)}%` : "—" },
              { label: "Odometer", value: v.odometer !== null ? `${v.odometer.toLocaleString()} km` : "—" },
              { label: "Status", value: v.status },
              {
                label: "Coordinates",
                value:
                  v.latitude !== null && v.longitude !== null
                    ? `${v.latitude?.toFixed(4)}, ${v.longitude?.toFixed(4)}`
                    : "—",
              },
            ].map(({ label, value }) => (
              <div key={label}>
                <div style={{ fontSize: 10, color: "#A1A7B1", textTransform: "uppercase", letterSpacing: "0.07em" }}>
                  {label}
                </div>
                <div style={{ fontSize: 13, color: "#F4F1E8", marginTop: 2, wordBreak: "break-all" }}>{value}</div>
              </div>
            ))}
          </div>

          {/* Telemetry history */}
          {detail.recent_telemetry.length > 0 && (
            <div>
              <div
                style={{
                  fontSize: 11,
                  color: "#A1A7B1",
                  textTransform: "uppercase",
                  letterSpacing: "0.07em",
                  marginBottom: 8,
                  fontWeight: 600,
                }}
              >
                Recent Telemetry
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {detail.recent_telemetry.slice(0, 5).map((t, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      gap: 8,
                      fontSize: 11,
                      color: "#A1A7B1",
                      padding: "4px 8px",
                      background: "#22262D",
                      borderRadius: 6,
                      fontVariantNumeric: "tabular-nums",
                      flexWrap: "wrap",
                    }}
                  >
                    <span style={{ color: "#F4F1E8", minWidth: 60 }}>
                      {t.speed !== null ? `${t.speed.toFixed(0)} km/h` : "—"}
                    </span>
                    <span>{t.fuel_level !== null ? `Fuel ${t.fuel_level.toFixed(0)}%` : ""}</span>
                    <span>{t.coolant_temp !== null ? `Coolant ${t.coolant_temp.toFixed(0)}°C` : ""}</span>
                    <span style={{ marginLeft: "auto" }}>
                      {t.timestamp ? new Date(t.timestamp).toLocaleTimeString("en-IN") : ""}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Anomaly injector */}
          <div>
            <div
              style={{
                fontSize: 11,
                color: "#A1A7B1",
                textTransform: "uppercase",
                letterSpacing: "0.07em",
                marginBottom: 8,
                fontWeight: 600,
              }}
            >
              Inject Anomaly
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {ANOMALIES.map((a) => (
                <button
                  key={a.value}
                  id={`inject-${a.value}-${v.id}`}
                  onClick={() => handleInject(a.value)}
                  disabled={!!injecting}
                  style={{
                    textAlign: "left",
                    padding: "7px 10px",
                    borderRadius: 7,
                    border: "1px solid",
                    borderColor: a.severity === "critical" ? "#D63B2F" : "#E5A33D",
                    background:
                      injecting === a.value
                        ? a.severity === "critical"
                          ? "rgba(214,59,47,0.15)"
                          : "rgba(229,163,61,0.15)"
                        : "transparent",
                    color: a.severity === "critical" ? "#D63B2F" : "#E5A33D",
                    fontSize: 11,
                    fontWeight: 500,
                    cursor: injecting ? "wait" : "pointer",
                    opacity: injecting && injecting !== a.value ? 0.4 : 1,
                    transition: "all 0.15s",
                  }}
                >
                  {injecting === a.value ? "⏳ " : ""}
                  {a.label}
                </button>
              ))}
            </div>
            {lastResult && (
              <div
                style={{
                  marginTop: 8,
                  fontSize: 12,
                  color: "#36B37E",
                  padding: "6px 10px",
                  background: "rgba(54,179,126,0.1)",
                  borderRadius: 6,
                  border: "1px solid rgba(54,179,126,0.2)",
                }}
              >
                ✓ {lastResult}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
