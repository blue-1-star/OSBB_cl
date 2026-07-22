"""
Новое ядро OSBB
Постепенно замещает старую архитектуру
"""

from .adapters import DBAdapter
from .domain import Vehicle, VehicleCandidate

__all__ = [
    'DBAdapter',
    'Vehicle',
    'VehicleCandidate',
]