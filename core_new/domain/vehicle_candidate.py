"""
Доменная модель: Кандидат в автомобили (VehicleCandidate)
Не требует квартиры, живёт в отдельной таблице, имеет статус жизненного цикла.
"""

from typing import Optional, Dict, Any
from datetime import datetime
from ..adapters.db_adapter import DBAdapter


class VehicleCandidate:
    """
    Кандидат в автомобили.
    
    Используется, когда:
    - Автомобиль ещё не привязан к квартире
    - Оператор принимает оплату, но номер квартиры неизвестен
    - Нужно создать «черновик» автомобиля для последующего подтверждения
    """
    
    def __init__(self, data: Dict[str, Any]):
        self.id = data.get('id')
        self.plate = data.get('license_plate')
        self.plate_norm = data.get('license_plate_normalized')
        self.model = data.get('car_model')
        self.model_norm = data.get('car_model_normalized')
        self.apartment_id = data.get('apartment_id')
        self.apartment_number = data.get('apartment_number')
        self.status = data.get('status', 'PENDING')
        self.resolved_vehicle_id = data.get('resolved_vehicle_id')
        self.merged_vehicle_id = data.get('merged_vehicle_id')
        self.created_by = data.get('created_by')
        self.created_at = data.get('created_at')
        self.updated_at = data.get('updated_at')
        self.comment = data.get('comment')
        self.source = data.get('source', 'cashier')
    
    @property
    def display_name(self) -> str:
        return f"{self.plate} | {self.model or '-'} | {self.status}"
    
    @property
    def is_pending(self) -> bool:
        return self.status == 'PENDING'
    
    @property
    def is_resolved(self) -> bool:
        return self.status == 'RESOLVED'
    
    @classmethod
    def create(
        cls,
        plate: str,
        model: Optional[str] = None,
        apartment_number: Optional[str] = None,
        created_by: Optional[int] = None,
        comment: Optional[str] = None,
        source: str = 'cashier'
    ) -> 'VehicleCandidate':
        """Создаёт нового кандидата в БД"""
        plate_norm = plate.upper().strip()
        
        candidate_id = DBAdapter.create_vehicle_candidate(
            plate=plate,
            plate_norm=plate_norm,
            model=model,
            apartment_number=apartment_number,
            created_by=created_by,
            comment=comment,
            source=source,
        )
        return cls.get_by_id(candidate_id)
    
    @classmethod
    def get_by_id(cls, candidate_id: int) -> Optional['VehicleCandidate']:
        data = DBAdapter.get_vehicle_candidate(candidate_id)
        return cls(data) if data else None
    
    @classmethod
    def get_by_plate(cls, plate: str) -> Optional['VehicleCandidate']:
        data = DBAdapter.find_vehicle_candidate_by_plate(plate)
        return cls(data) if data else None
    
    def resolve(self, apartment_number: str) -> tuple:
        """
        Подтвердить кандидата → создать полноценный автомобиль в `vehicles`
        """
        from .vehicles import Vehicle
        
        # Создаём автомобиль
        vehicle = Vehicle.create(
            plate=self.plate,
            model=self.model,
            apartment_number=apartment_number or self.apartment_number,
        )
        
        # Обновляем статус кандидата
        self.status = 'RESOLVED'
        self.resolved_vehicle_id = vehicle.id
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        DBAdapter.resolve_vehicle_candidate(self.id, vehicle.id)
        
        return True, vehicle
    
    def reject(self, reason: Optional[str] = None) -> tuple:
        """Отклонить кандидата"""
        self.status = 'REJECTED'
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if reason:
            self.comment = reason
        return DBAdapter.update_candidate_status(self.id, 'REJECTED')