from typing import Optional, List, Dict, Any
from ..adapters.db_adapter import DBAdapter


class Vehicle:
    """Модель автомобиля"""
    
    def __init__(self, data: Dict[str, Any]):
        self.id = data.get('id')
        self.apartment_number = data.get('apartment_number')
        self.plate = data.get('plate_norm') or data.get('plate_raw') or '-'
        self.model = data.get('model_norm') or data.get('model_raw') or '-'
        self.parking_time = data.get('parking_time')
    
    @property
    def display_name(self) -> str:
        return f"{self.plate} | {self.model}"
    
    @property
    def status_display(self) -> str:
        if not self.parking_time:
            return "❓ Не указан"
        if self.parking_time == 'Day':
            return "☀️ День"
        if self.parking_time == 'Night':
            return "🌙 Ночь"
        if self.parking_time == 'Inactive':
            return "🚫 Не паркуется"
        return f"⚠️ {self.parking_time}"
    
    @classmethod
    def get_by_id(cls, vehicle_id: int) -> Optional['Vehicle']:
        data = DBAdapter.get_vehicle(vehicle_id)
        return cls(data) if data else None
    
    @classmethod
    def get_by_apartment(cls, apartment: str) -> List['Vehicle']:
        data_list = DBAdapter.get_apartment_vehicles(apartment)
        return [cls(d) for d in data_list]
    
    def set_parking_time(self, status: str) -> tuple:
        return DBAdapter.set_vehicle_parking_status(self.id, status)


class VehicleCandidate:
    """Кандидат автомобиля (ещё не привязан к квартире)"""
    
    def __init__(self, plate: str, model: Optional[str] = None):
        self.plate = plate.upper().strip()
        self.model = model.upper().strip() if model else None
        self.status = 'pending'
    
    @property
    def display_name(self) -> str:
        model_part = f" ({self.model})" if self.model else ""
        return f"{self.plate}{model_part} [{self.status}]"
    
    def approve(self, apartment_number: str) -> tuple:
        apt = DBAdapter.find_apartment(apartment_number)
        if not apt:
            return False, f"Квартира {apartment_number} не найдена"
        
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        try:
            cur.execute("""
                INSERT INTO vehicles (
                    apartment_id,
                    license_plate,
                    license_plate_normalized,
                    car_model,
                    car_model_normalized
                )
                VALUES (?, ?, ?, ?, ?)
            """, (apt['id'], self.plate, self.plate, self.model, self.model))
            
            vehicle_id = cur.lastrowid
            conn.commit()
            
            self.status = 'approved'
            return True, Vehicle.get_by_id(vehicle_id)
            
        except Exception as e:
            conn.rollback()
            return False, f"Ошибка: {e}"
        finally:
            conn.close()
