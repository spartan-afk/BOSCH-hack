// Fleet Management API client — runtime only (types in types.ts)
import type { FleetStatus, Vehicle, Alert, VehicleDetail } from "./types";

export const API_URL = "http://localhost:8000/api";

// ── HTTP helpers ─────────────────────────────────────────────────────────────

async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`);
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

async function post<T>(path: string, body?: unknown): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

async function put<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, { method: "PUT" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

// ── Fleet API ─────────────────────────────────────────────────────────────────

export const fleetApi = {
  getStatus: () => get<FleetStatus>("/fleet/status"),
  getVehicles: () => get<Vehicle[]>("/fleet/vehicles"),
  getVehicleDetail: (id: number) => get<VehicleDetail>(`/fleet/vehicles/${id}`),
  getAlerts: (opts?: { severity?: string; unacknowledged_only?: boolean; limit?: number }) => {
    const params = new URLSearchParams();
    if (opts?.severity) params.set("severity", opts.severity);
    if (opts?.unacknowledged_only) params.set("unacknowledged_only", "true");
    if (opts?.limit) params.set("limit", String(opts.limit));
    const qs = params.toString();
    return get<Alert[]>(`/fleet/alerts${qs ? `?${qs}` : ""}`);
  },
  acknowledgeAlert: (id: number) =>
    put<{ status: string; alert_id: number }>(`/fleet/alerts/${id}/acknowledge`),
  toggleSimulation: () =>
    post<{ status: string; simulation_running: boolean }>("/fleet/simulate/toggle"),
  triggerAnomaly: (vehicle_id: number, anomaly_type: string) =>
    post<{ status: string; alerts_triggered: number; alerts: { type: string; severity: string; message: string }[] }>(
      "/fleet/simulate/anomaly",
      { vehicle_id, anomaly_type }
    ),
};
