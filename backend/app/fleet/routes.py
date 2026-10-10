"""FastAPI routes for Fleet Management System — vehicles, telematics, alerts, and simulation."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.fleet.engine import FleetEngine
from app.fleet.models.alert import Alert, AlertSeverity, AlertType
from app.fleet.models.database import get_db
from app.fleet.models.telemetry import TelemetryData
from app.fleet.models.vehicle import Vehicle, VehicleStatus

router = APIRouter(prefix="/fleet", tags=["Fleet Management"])

# Global Fleet Engine instance
fleet_engine = FleetEngine()


# ── Schemas ───────────────────────────────────────────────────────────────

class AnomalyRequest(BaseModel):
    vehicle_id: int = 1
    anomaly_type: str = "overheating"  # speeding, overheating, harsh_braking, dtc_misfire, low_fuel, high_rpm


class TelemetryIngestPayload(BaseModel):
    vehicle_id: int
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    vehicle_speed: Optional[float] = None
    engine_rpm: Optional[float] = None
    coolant_temp: Optional[float] = None
    fuel_level: Optional[float] = None
    battery_voltage: Optional[float] = None
    dtc_codes: Optional[list] = None
    acceleration_y: Optional[float] = None
    odometer: Optional[float] = None


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/status")
def get_fleet_status(db: Session = Depends(get_db)):
    """Return high-level summary of fleet health, vehicles, and alert counts."""
    total_vehicles = db.query(Vehicle).count()
    active_vehicles = db.query(Vehicle).filter(Vehicle.status == VehicleStatus.ACTIVE).count()
    
    since_24h = datetime.utcnow() - timedelta(hours=24)
    alerts_24h = db.query(Alert).filter(Alert.created_at >= since_24h).all()
    
    critical_count = sum(1 for a in alerts_24h if a.severity == AlertSeverity.CRITICAL)
    warning_count = sum(1 for a in alerts_24h if a.severity == AlertSeverity.WARNING)
    unacknowledged = sum(1 for a in alerts_24h if not a.is_acknowledged)

    return {
        "status": "online",
        "system_name": "Fleet Management System",
        "simulation_running": fleet_engine.running,
        "vehicles": {
            "total": total_vehicles,
            "active": active_vehicles,
            "idle": total_vehicles - active_vehicles,
        },
        "alerts_24h": {
            "total": len(alerts_24h),
            "critical": critical_count,
            "warning": warning_count,
            "unacknowledged": unacknowledged,
        },
        "recent_live_alerts": fleet_engine.last_alerts[-10:],
    }


@router.get("/vehicles")
def list_vehicles(db: Session = Depends(get_db)):
    """List all vehicles in the fleet with their latest telemetry state."""
    vehicles = db.query(Vehicle).all()
    results = []
    for v in vehicles:
        results.append({
            "id": v.id,
            "vin": v.vin,
            "license_plate": v.license_plate,
            "make": v.make,
            "model": v.model,
            "year": v.year,
            "color": v.color,
            "fuel_type": v.fuel_type.value if hasattr(v.fuel_type, "value") else str(v.fuel_type),
            "fleet_group": v.fleet_group,
            "status": v.status.value if hasattr(v.status, "value") else str(v.status),
            "current_latitude": v.current_latitude,
            "current_longitude": v.current_longitude,
            "current_speed": v.current_speed,
            "current_fuel_level": v.current_fuel_level,
            "odometer": v.odometer,
            "last_telemetry_at": v.last_telemetry_at.isoformat() if v.last_telemetry_at else None,
        })
    return results


@router.get("/vehicles/{vehicle_id}")
def get_vehicle_details(vehicle_id: int, db: Session = Depends(get_db)):
    """Get single vehicle details and its latest 20 telemetry records."""
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(404, f"Vehicle {vehicle_id} not found")

    recent_telemetry = (
        db.query(TelemetryData)
        .filter(TelemetryData.vehicle_id == vehicle_id)
        .order_by(desc(TelemetryData.timestamp))
        .limit(20)
        .all()
    )

    recent_alerts = (
        db.query(Alert)
        .filter(Alert.vehicle_id == vehicle_id)
        .order_by(desc(Alert.created_at))
        .limit(10)
        .all()
    )

    return {
        "vehicle": {
            "id": vehicle.id,
            "vin": vehicle.vin,
            "license_plate": vehicle.license_plate,
            "make": vehicle.make,
            "model": vehicle.model,
            "status": vehicle.status.value if hasattr(vehicle.status, "value") else str(vehicle.status),
            "current_speed": vehicle.current_speed,
            "current_fuel_level": vehicle.current_fuel_level,
            "odometer": vehicle.odometer,
            "latitude": vehicle.current_latitude,
            "longitude": vehicle.current_longitude,
        },
        "recent_telemetry": [
            {
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                "speed": t.vehicle_speed,
                "rpm": t.engine_rpm,
                "coolant_temp": t.coolant_temp,
                "fuel_level": t.fuel_level,
            }
            for t in recent_telemetry
        ],
        "recent_alerts": [
            {
                "id": a.id,
                "type": a.alert_type.value if hasattr(a.alert_type, "value") else str(a.alert_type),
                "severity": a.severity.value if hasattr(a.severity, "value") else str(a.severity),
                "message": a.message,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "is_acknowledged": a.is_acknowledged,
            }
            for a in recent_alerts
        ],
    }


@router.get("/alerts")
def list_alerts(
    vehicle_id: Optional[int] = None,
    severity: Optional[str] = None,
    unacknowledged_only: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Retrieve alerts generated from vehicle telematics."""
    query = db.query(Alert)
    if vehicle_id:
        query = query.filter(Alert.vehicle_id == vehicle_id)
    if severity:
        query = query.filter(Alert.severity == severity)
    if unacknowledged_only:
        query = query.filter(Alert.is_acknowledged == False)

    alerts = query.order_by(desc(Alert.created_at)).limit(limit).all()
    return [
        {
            "id": a.id,
            "vehicle_id": a.vehicle_id,
            "alert_type": a.alert_type.value if hasattr(a.alert_type, "value") else str(a.alert_type),
            "severity": a.severity.value if hasattr(a.severity, "value") else str(a.severity),
            "message": a.message,
            "details": a.details,
            "latitude": a.latitude,
            "longitude": a.longitude,
            "trigger_value": a.trigger_value,
            "threshold_value": a.threshold_value,
            "is_acknowledged": a.is_acknowledged,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in alerts
    ]


@router.put("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, db: Session = Depends(get_db)):
    """Acknowledge an alert."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(404, "Alert not found")
    alert.is_acknowledged = True
    alert.acknowledged_at = datetime.utcnow()
    alert.acknowledged_by = "operator"
    db.commit()
    return {"status": "acknowledged", "alert_id": alert_id}


@router.post("/simulate/toggle")
async def toggle_simulation():
    """Toggle the continuous telematics simulator on or off."""
    if fleet_engine.running:
        await fleet_engine.stop()
        return {"status": "paused", "simulation_running": False}
    else:
        await fleet_engine.start()
        return {"status": "started", "simulation_running": True}


@router.post("/simulate/anomaly")
def trigger_simulated_anomaly(req: AnomalyRequest):
    """Inject a simulated telemetry anomaly to trigger real-time alerts.
    
    Supported types:
    - 'speeding': 158 km/h -> SPEEDING_VIOLATION critical alert
    - 'overheating': 119.5 °C -> ENGINE_OVERHEATING critical alert
    - 'harsh_braking': -11.5 m/s² -> HARSH_BRAKING warning alert
    - 'dtc_misfire': P0301 code -> DTC_DETECTED warning alert
    - 'low_fuel': 4% fuel -> LOW_FUEL critical alert
    - 'high_rpm': 6800 RPM -> HIGH_RPM warning alert
    """
    res = fleet_engine.trigger_anomaly(req.vehicle_id, req.anomaly_type)
    return res


@router.post("/telemetry/ingest")
def ingest_telemetry_payload(data: TelemetryIngestPayload, db: Session = Depends(get_db)):
    """Directly ingest telematics payload and evaluate alerts."""
    vehicle = db.query(Vehicle).filter(Vehicle.id == data.vehicle_id).first()
    if not vehicle:
        raise HTTPException(404, f"Vehicle {data.vehicle_id} not found")

    payload_dict = data.model_dump()
    alerts = fleet_engine._process_telemetry(db, data.vehicle_id, payload_dict)
    db.commit()

    return {
        "status": "ingested",
        "vehicle_id": data.vehicle_id,
        "alerts_triggered": len(alerts),
        "alerts": [
            {
                "type": a.alert_type.value if hasattr(a.alert_type, "value") else str(a.alert_type),
                "severity": a.severity.value if hasattr(a.severity, "value") else str(a.severity),
                "message": a.message,
            }
            for a in alerts
        ],
    }
