from app.fleet.models.database import Base, get_db, engine, SessionLocal
from app.fleet.models.vehicle import Vehicle
from app.fleet.models.telemetry import TelemetryData
from app.fleet.models.driver import Driver
from app.fleet.models.alert import Alert
from app.fleet.models.trip import Trip
from app.fleet.models.maintenance import MaintenanceRecord
from app.fleet.models.geofence import Geofence

__all__ = [
    "Base", "get_db", "engine", "SessionLocal",
    "Vehicle", "TelemetryData", "Driver", "Alert",
    "Trip", "MaintenanceRecord", "Geofence",
]
