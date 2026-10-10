// Fleet Management — shared TypeScript types
export interface FleetStatus {
  status: string;
  system_name: string;
  simulation_running: boolean;
  vehicles: { total: number; active: number; idle: number };
  alerts_24h: { total: number; critical: number; warning: number; unacknowledged: number };
  recent_live_alerts: LiveAlert[];
}

export interface LiveAlert {
  vehicle_id: number;
  type: string;
  severity: string;
  message: string;
  timestamp: string;
}

export interface Vehicle {
  id: number;
  vin: string;
  license_plate: string;
  make: string;
  model: string;
  year: number;
  color: string;
  fuel_type: string;
  fleet_group: string;
  status: string;
  current_latitude: number | null;
  current_longitude: number | null;
  current_speed: number | null;
  current_fuel_level: number | null;
  odometer: number | null;
  last_telemetry_at: string | null;
}

export interface Alert {
  id: number;
  vehicle_id: number;
  alert_type: string;
  severity: string;
  message: string;
  details: string | null;
  latitude: number | null;
  longitude: number | null;
  trigger_value: number | null;
  threshold_value: number | null;
  is_acknowledged: boolean;
  created_at: string | null;
}

export interface VehicleDetail {
  vehicle: {
    id: number;
    vin: string;
    license_plate: string;
    make: string;
    model: string;
    status: string;
    current_speed: number | null;
    current_fuel_level: number | null;
    odometer: number | null;
    latitude: number | null;
    longitude: number | null;
  };
  recent_telemetry: {
    timestamp: string | null;
    speed: number | null;
    rpm: number | null;
    coolant_temp: number | null;
    fuel_level: number | null;
  }[];
  recent_alerts: {
    id: number;
    type: string;
    severity: string;
    message: string;
    created_at: string | null;
    is_acknowledged: boolean;
  }[];
}
