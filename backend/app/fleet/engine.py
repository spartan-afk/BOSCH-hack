"""Fleet Manager Engine — Background telemetry simulator, seeder, and alert coordinator."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.fleet.config.settings import settings
from app.fleet.models.database import SessionLocal, init_db
from app.fleet.models.vehicle import Vehicle, VehicleStatus, FuelType
from app.fleet.models.driver import Driver
from app.fleet.models.telemetry import TelemetryData
from app.fleet.models.alert import Alert, AlertType, AlertSeverity
from app.fleet.telematics.alert_engine import AlertEngine

logger = logging.getLogger("fleet_engine")


class FleetEngine:
    """Coordinates fleet seeding, live telemetry simulation, and alert generation."""

    def __init__(self):
        self.alert_engine = AlertEngine()
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._simulators: Dict[int, Any] = {}
        self.last_alerts: List[Dict[str, Any]] = []

    def initialize(self):
        """Initialize tables and seed default fleet if empty."""
        init_db()
        self.seed_defaults()
        self._init_simulators()

    def seed_defaults(self):
        """Seed initial vehicles and drivers if none exist."""
        db = SessionLocal()
        try:
            if db.query(Vehicle).count() > 0:
                logger.info("Fleet database already populated.")
                return

            logger.info("Seeding initial fleet vehicles and drivers...")
            vehicles = [
                Vehicle(
                    vin="1HGBH41JXMN109186",
                    license_plate="KA-01-AB-1234",
                    make="Toyota",
                    model="Camry",
                    year=2024,
                    color="White",
                    fuel_type=FuelType.PETROL,
                    engine_capacity=2.5,
                    fleet_group="executive",
                    status=VehicleStatus.ACTIVE,
                    current_fuel_level=78.0,
                    odometer=14200,
                    current_latitude=12.9716,
                    current_longitude=77.5946,
                    current_speed=54.0,
                ),
                Vehicle(
                    vin="2T1BURHE5JC067841",
                    license_plate="KA-02-CD-5678",
                    make="Tata",
                    model="Nexon EV",
                    year=2025,
                    color="Blue",
                    fuel_type=FuelType.ELECTRIC,
                    engine_capacity=0.0,
                    fleet_group="delivery",
                    status=VehicleStatus.ACTIVE,
                    current_fuel_level=88.0,
                    odometer=6500,
                    current_latitude=12.9352,
                    current_longitude=77.6245,
                    current_speed=42.0,
                ),
                Vehicle(
                    vin="5YJSA1E26MF123456",
                    license_plate="KA-03-EF-9012",
                    make="Mahindra",
                    model="XUV700",
                    year=2024,
                    color="Red",
                    fuel_type=FuelType.DIESEL,
                    engine_capacity=2.2,
                    fleet_group="field_ops",
                    status=VehicleStatus.ACTIVE,
                    current_fuel_level=52.0,
                    odometer=38900,
                    current_latitude=12.9899,
                    current_longitude=77.7126,
                    current_speed=68.0,
                ),
                Vehicle(
                    vin="WBAJB0C55JB084299",
                    license_plate="MH-01-GH-3456",
                    make="Maruti Suzuki",
                    model="Swift",
                    year=2024,
                    color="Silver",
                    fuel_type=FuelType.PETROL,
                    engine_capacity=1.2,
                    fleet_group="delivery",
                    status=VehicleStatus.ACTIVE,
                    current_fuel_level=64.0,
                    odometer=21300,
                    current_latitude=13.0358,
                    current_longitude=77.5970,
                    current_speed=38.0,
                ),
                Vehicle(
                    vin="JN1TBNT30Z0000001",
                    license_plate="DL-01-IJ-7890",
                    make="Tata",
                    model="Ace Gold",
                    year=2023,
                    color="White",
                    fuel_type=FuelType.CNG,
                    engine_capacity=0.8,
                    fleet_group="logistics",
                    status=VehicleStatus.IDLE,
                    current_fuel_level=35.0,
                    odometer=48500,
                    current_latitude=12.9141,
                    current_longitude=77.6109,
                    current_speed=0.0,
                ),
            ]
            for v in vehicles:
                db.add(v)

            drivers = [
                Driver(
                    first_name="Rajesh",
                    last_name="Kumar",
                    employee_id="DRV001",
                    email="rajesh@fleet.com",
                    phone="+91-9876543210",
                    license_number="KA-0120230012345",
                    safety_score=94.0,
                ),
                Driver(
                    first_name="Ananya",
                    last_name="Iyer",
                    employee_id="DRV002",
                    email="ananya@fleet.com",
                    phone="+91-9876543211",
                    license_number="KA-0220240098765",
                    safety_score=89.5,
                ),
            ]
            for d in drivers:
                db.add(d)

            db.commit()
            logger.info("Fleet seeded successfully.")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to seed fleet: {e}")
        finally:
            db.close()

    def _init_simulators(self):
        """Initialize vehicle simulator instances for each vehicle."""
        from app.fleet.simulator import VehicleSimulator

        db = SessionLocal()
        try:
            vehicles = db.query(Vehicle).all()
            for v in vehicles:
                lat = v.current_latitude or 12.9716
                lon = v.current_longitude or 77.5946
                sim = VehicleSimulator(vehicle_id=v.id, start_lat=lat, start_lon=lon)
                self._simulators[v.id] = sim
        finally:
            db.close()

    async def start(self):
        """Start the background telematics simulation loop."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._simulation_loop())
        logger.info("Fleet background telematics simulation started.")

    async def stop(self):
        """Stop background telematics simulator."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Fleet background telematics simulation stopped.")

    async def _simulation_loop(self):
        """Tick simulated vehicles periodically and persist telemetry + alerts."""
        while self.running:
            try:
                db = SessionLocal()
                try:
                    for vid, sim in list(self._simulators.items()):
                        telemetry_data = sim.tick()
                        self._process_telemetry(db, vid, telemetry_data)
                    db.commit()
                except Exception as e:
                    db.rollback()
                    logger.error(f"Error in fleet simulation tick: {e}")
                finally:
                    db.close()

                await asyncio.sleep(3.0)  # Telemetry updates every 3 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected simulation loop error: {e}")
                await asyncio.sleep(3.0)

    def _process_telemetry(self, db, vehicle_id: int, telemetry_dict: Dict[str, Any]) -> List[Alert]:
        """Ingest telemetry data, evaluate alerts, and update vehicle state."""
        vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
        if not vehicle:
            return []

        # Store Telemetry Record
        telemetry = TelemetryData(**telemetry_dict)
        db.add(telemetry)

        # Update Vehicle state
        if telemetry_dict.get("latitude"):
            vehicle.current_latitude = telemetry_dict["latitude"]
        if telemetry_dict.get("longitude"):
            vehicle.current_longitude = telemetry_dict["longitude"]
        if telemetry_dict.get("vehicle_speed") is not None:
            vehicle.current_speed = telemetry_dict["vehicle_speed"]
        if telemetry_dict.get("fuel_level") is not None:
            vehicle.current_fuel_level = telemetry_dict["fuel_level"]
        if telemetry_dict.get("odometer") is not None:
            vehicle.odometer = telemetry_dict["odometer"]
        vehicle.last_telemetry_at = datetime.utcnow()

        # Evaluate alerts
        triggered = self.alert_engine.evaluate(vehicle_id, telemetry_dict)
        alerts_saved = []

        for a in triggered:
            alert = Alert(
                vehicle_id=a["vehicle_id"],
                alert_type=a["alert_type"],
                severity=a["severity"],
                message=a["message"],
                details=a.get("details"),
                latitude=a.get("latitude"),
                longitude=a.get("longitude"),
                trigger_value=a.get("trigger_value"),
                threshold_value=a.get("threshold_value"),
            )
            db.add(alert)
            alerts_saved.append(alert)
            self.last_alerts.append({
                "vehicle_id": vehicle_id,
                "type": a["alert_type"].value,
                "severity": a["severity"].value,
                "message": a["message"],
                "timestamp": datetime.utcnow().isoformat(),
            })

        # Keep last 50 in memory
        if len(self.last_alerts) > 50:
            self.last_alerts = self.last_alerts[-50:]

        return alerts_saved

    def trigger_anomaly(self, vehicle_id: int, anomaly_type: str) -> Dict[str, Any]:
        """Manually inject a telemetry anomaly to trigger specific alerts on demand."""
        sim = self._simulators.get(vehicle_id)
        if not sim:
            return {"error": f"Simulator not found for vehicle {vehicle_id}"}

        db = SessionLocal()
        try:
            snapshot = sim.tick()

            if anomaly_type == "speeding":
                snapshot["vehicle_speed"] = 158.0
                snapshot["gps_speed"] = 158.0
            elif anomaly_type == "overheating":
                snapshot["coolant_temp"] = 119.5
                snapshot["engine_oil_temp"] = 125.0
            elif anomaly_type == "harsh_braking":
                snapshot["acceleration_y"] = -11.5
                snapshot["vehicle_speed"] = 65.0
            elif anomaly_type == "dtc_misfire":
                snapshot["dtc_codes"] = ["P0301", "P0300"]
            elif anomaly_type == "low_fuel":
                snapshot["fuel_level"] = 4.0
            elif anomaly_type == "high_rpm":
                snapshot["engine_rpm"] = 6800.0

            alerts = self._process_telemetry(db, vehicle_id, snapshot)
            db.commit()

            return {
                "status": "anomaly_injected",
                "vehicle_id": vehicle_id,
                "anomaly_type": anomaly_type,
                "alerts_triggered": len(alerts),
                "alerts": [
                    {"type": a.alert_type.value, "severity": a.severity.value, "message": a.message}
                    for a in alerts
                ],
            }
        except Exception as e:
            db.rollback()
            logger.error(f"Anomaly injection failed: {e}")
            return {"error": str(e)}
        finally:
            db.close()
