"""
Адаптер для кассы — связывает старую кассу с новой архитектурой
"""

from typing import Optional, Dict, Any
from ..domain.vehicles import Vehicle, VehicleCandidate
from ..domain.payments import Payment
from ..domain.service_catalog import ServiceCatalog
from ..domain.cashbox import Cashbox


class CashierAdapter:
    """
    Адаптер для кассы.
    Использует новую архитектуру, но сохраняет старый интерфейс.
    """
    
    @staticmethod
    def register_payment(
        amount: float,
        plate: str,
        apartment_number: str,
        service_code: str = ServiceCatalog.PARKING_DAY,
        payment_method: str = 'cash',
        operator_id: Optional[int] = None,
        comment: str = None,
    ) -> Dict[str, Any]:
        """
        Регистрирует платёж через новую архитектуру.
        """
        return Cashbox.register_payment(
            amount=amount,
            plate=plate,
            apartment_number=apartment_number,
            service_code=service_code,
            payment_method=payment_method,
            operator_id=operator_id,
            comment=comment,
        )
    
    @staticmethod
    def get_daily_report(date: str = None) -> Dict[str, Any]:
        """Получить отчёт за день"""
        return Cashbox.get_daily_report(date)