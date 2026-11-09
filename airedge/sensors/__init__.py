"""Датчики: абстракція та імітатор."""
from .base import Sensor, SensorError
from .simulated import FaultProfile, SimulatedSensor

__all__ = ["Sensor", "SensorError", "FaultProfile", "SimulatedSensor"]
