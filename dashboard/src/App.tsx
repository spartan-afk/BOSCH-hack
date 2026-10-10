import { useState, useEffect, useCallback, useRef } from "react";
import type { FleetStatus, Vehicle, Alert, VehicleDetail } from "./types";
import { fleetApi } from "./api";
import { FleetHeader } from "./components/FleetHeader";
import { SummaryCards } from "./components/SummaryCards";
import { VehicleTable } from "./components/VehicleTable";
import { AlertsPanel } from "./components/AlertsPanel";
import { VehicleDetailPanel } from "./components/VehicleDetailPanel";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./index.css";

const POLL_INTERVAL_MS = 5000;

export default function App() {
  const [status, setStatus] = useState<FleetStatus | null>(null);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [selectedVehicleId, setSelectedVehicleId] = useState<number | null>(null);
  const [vehicleDetail, setVehicleDetail] = useState<VehicleDetail | null>(null);

  const [loadingStatus, setLoadingStatus] = useState(true);
  const [loadingVehicles, setLoadingVehicles] = useState(true);
  const [loadingAlerts, setLoadingAlerts] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [simToggling, setSimToggling] = useState(false);

  // Sidebar: "alerts" | "detail" | null
  const [activePanel, setActivePanel] = useState<"alerts" | "detail">("alerts");

  const alertsPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Data fetchers ────────────────────────────────────────────────────────

  const fetchStatus = useCallback(async () => {
    const s = await fleetApi.getStatus();
    if (s) setStatus(s);
    setLoadingStatus(false);
  }, []);

  const fetchVehicles = useCallback(async () => {
    const v = await fleetApi.getVehicles();
    if (v) setVehicles(v);
    setLoadingVehicles(false);
  }, []);

  const fetchAlerts = useCallback(async () => {
    const a = await fleetApi.getAlerts({ limit: 80 });
    if (a) setAlerts(a);
    setLoadingAlerts(false);
    setLastUpdated(new Date());
  }, []);

  const fetchVehicleDetail = useCallback(async (id: number) => {
    setLoadingDetail(true);
    const d = await fleetApi.getVehicleDetail(id);
    setVehicleDetail(d);
    setLoadingDetail(false);
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.all([fetchStatus(), fetchVehicles(), fetchAlerts()]);
  }, [fetchStatus, fetchVehicles, fetchAlerts]);

  // ── Initial load + polling ───────────────────────────────────────────────

  useEffect(() => {
    refreshAll();
    const id = setInterval(refreshAll, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refreshAll]);

  // ── Vehicle selection ────────────────────────────────────────────────────

  const handleSelectVehicle = (id: number) => {
    if (id === selectedVehicleId) {
      setSelectedVehicleId(null);
      setVehicleDetail(null);
      setActivePanel("alerts");
    } else {
      setSelectedVehicleId(id);
      setActivePanel("detail");
      fetchVehicleDetail(id);
    }
  };

  const handleCloseDetail = () => {
    setSelectedVehicleId(null);
    setVehicleDetail(null);
    setActivePanel("alerts");
  };

  // ── Simulation toggle ────────────────────────────────────────────────────

  const handleSimToggle = async () => {
    setSimToggling(true);
    await fleetApi.toggleSimulation();
    await fetchStatus();
    setSimToggling(false);
  };

  // ── Notification bell → switch to alerts panel ───────────────────────────

  const unackCount = alerts.filter((a) => !a.is_acknowledged).length;

  const handleNotificationsClick = () => {
    setActivePanel("alerts");
    setSelectedVehicleId(null);
    setVehicleDetail(null);
  };

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div style={{ minHeight: "100vh", background: "#0D0F12", display: "flex", flexDirection: "column" }}>
      <ErrorBoundary>
        <FleetHeader
          status={status}
          lastUpdated={lastUpdated}
          unreadAlerts={unackCount}
          onNotificationsClick={handleNotificationsClick}
        />
      </ErrorBoundary>

      {/* ── Main layout ───────────────────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          maxWidth: 1600,
          width: "100%",
          margin: "0 auto",
          padding: "20px 24px",
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        {/* Summary Cards */}
        <ErrorBoundary>
          <SummaryCards status={loadingStatus ? null : status} />
        </ErrorBoundary>

        {/* Simulation Control Bar */}
        <div
          style={{
            background: "#191D23",
            border: "1px solid #30353E",
            borderRadius: 10,
            padding: "10px 18px",
            display: "flex",
            alignItems: "center",
            gap: 16,
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontSize: 12, color: "#A1A7B1", flex: 1 }}>
            Telematics simulation runs in the background, updating vehicle positions and triggering alerts every ~3s.
          </span>
          <button
            id="sim-toggle-btn"
            onClick={handleSimToggle}
            disabled={simToggling}
            style={{
              fontSize: 12,
              fontWeight: 600,
              padding: "6px 16px",
              borderRadius: 7,
              border: "1px solid",
              cursor: simToggling ? "wait" : "pointer",
              transition: "all 0.15s",
              borderColor: status?.simulation_running ? "#D63B2F" : "#36B37E",
              color: status?.simulation_running ? "#D63B2F" : "#36B37E",
              background: status?.simulation_running
                ? "rgba(214,59,47,0.08)"
                : "rgba(54,179,126,0.08)",
              opacity: simToggling ? 0.6 : 1,
            }}
          >
            {simToggling ? "…" : status?.simulation_running ? "⏹ Pause Simulation" : "▶ Start Simulation"}
          </button>
          <button
            id="refresh-all-btn"
            onClick={refreshAll}
            style={{
              fontSize: 12,
              fontWeight: 500,
              padding: "6px 12px",
              borderRadius: 7,
              border: "1px solid #30353E",
              color: "#A1A7B1",
              background: "transparent",
              cursor: "pointer",
            }}
          >
            ↻ Refresh
          </button>
        </div>

        {/* Content grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: activePanel === "alerts"
              ? "1fr 360px"
              : activePanel === "detail"
              ? "1fr 340px"
              : "1fr",
            gap: 16,
            alignItems: "start",
          }}
        >
          {/* Left: vehicles */}
          <ErrorBoundary>
            <VehicleTable
              vehicles={vehicles}
              selectedId={selectedVehicleId}
              onSelect={handleSelectVehicle}
              loading={loadingVehicles}
            />
          </ErrorBoundary>

          {/* Right: alerts or detail */}
          <div className="fade-in">
            <ErrorBoundary>
              {activePanel === "alerts" ? (
                <AlertsPanel
                  alerts={alerts}
                  loading={loadingAlerts}
                  onRefresh={fetchAlerts}
                />
              ) : (
                <VehicleDetailPanel
                  detail={vehicleDetail}
                  loading={loadingDetail}
                  onClose={handleCloseDetail}
                />
              )}
            </ErrorBoundary>
          </div>
        </div>
      </div>
    </div>
  );
}
