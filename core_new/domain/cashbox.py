"""
Доменная модель: Касса (Cashbox)
Обеспечивает приём платежей с поддержкой "недоавтомобилей" через vehicle_candidates
"""

from typing import Optional, Dict, Any
from datetime import datetime
from ..adapters.db_adapter import DBAdapter
from .vehicles import Vehicle
from .payments import Payment
from .service_catalog import ServiceCatalog


class Cashbox:
    """
    Касса — рабочее место оператора для приёма платежей.
    """
    
    @staticmethod
    def register_payment(
        amount: float,
        plate: str,
        service_code: str,  # ← теперь ОБЯЗАТЕЛЬНЫЙ параметр
        apartment_number: Optional[str] = None,
        payment_method: str = 'cash',
        operator_id: Optional[int] = None,
        comment: Optional[str] = None,
        period_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Регистрирует платёж.
        
        Args:
            amount: сумма платежа
            plate: номер автомобиля
            service_code: код услуги из ServiceCatalog (ОБЯЗАТЕЛЬНО)
            apartment_number: номер квартиры (может быть None)
            payment_method: способ оплаты (cash, card, bank)
            operator_id: ID оператора (Telegram ID)
            comment: комментарий к платежу
            period_code: период начисления (если None — текущий месяц)
        """
        plate_norm = plate.upper().strip()
        result = {
            'success': False,
            'payment_id': None,
            'candidate_id': None,
            'vehicle_id': None,
            'vehicle_created': False,
            'candidate_created': False,
            'error': None,
        }
        
        # ==========================================
        # 1. Проверяем сумму
        # ==========================================
        if amount <= 0:
            result['error'] = 'Сумма должна быть больше 0'
            return result
        
        # ==========================================
        # 2. Проверяем услугу (через динамический каталог)
        # ==========================================
        if not ServiceCatalog.is_valid(service_code):
            result['error'] = f'Неизвестный код услуги: {service_code}'
            return result
        
        # ==========================================
        # 3. Ищем автомобиль
        # ==========================================
        vehicle = None
        vehicle_id = None
        
        # 3.1 Сначала точный поиск
        vehicle = DBAdapter.find_vehicle_by_plate(plate_norm)
        
        # 3.2 Если не найден — поиск по числовой части
        if not vehicle and plate_norm.isdigit():
            from Bots.db_access import get_conn
            conn = get_conn()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 
                    id,
                    license_plate_normalized,
                    license_plate,
                    car_model_normalized,
                    car_model,
                    parking_time
                FROM vehicles
                WHERE license_plate_normalized LIKE ?
                   OR license_plate LIKE ?
                ORDER BY id
                LIMIT 1
            """, (f'%{plate_norm}%', f'%{plate_norm}%'))
            
            row = cur.fetchone()
            conn.close()
            
            if row:
                vehicle = {
                    'id': row[0],
                    'plate_norm': row[1],
                    'plate_raw': row[2],
                    'model_norm': row[3],
                    'model_raw': row[4],
                    'parking_time': row[5],
                }
        
        # ==========================================
        # 4. Если автомобиль найден
        # ==========================================
        if vehicle:
            vehicle_id = vehicle['id']
            result['vehicle_id'] = vehicle_id
            if not apartment_number:
                from Bots.db_access import get_conn
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("""
                    SELECT apartment_number 
                    FROM vehicles v
                    JOIN apartments a ON a.id = v.apartment_id
                    WHERE v.id = ?
                """, (vehicle_id,))
                row = cur.fetchone()
                conn.close()
                if row:
                    apartment_number = row[0]
        
        # ==========================================
        # 5. Если автомобиль не найден — создаём кандидата
        # ==========================================
        candidate_id = None
        candidate_created = False
        
        if not vehicle:
            candidate = DBAdapter.find_vehicle_candidate_by_plate(plate_norm)
            
            if candidate:
                candidate_id = candidate['id']
                result['candidate_id'] = candidate_id
                if not apartment_number:
                    apartment_number = candidate.get('apartment_number')
            else:
                candidate_id = DBAdapter.create_vehicle_candidate(
                    plate=plate,
                    plate_norm=plate_norm,
                    model=None,
                    apartment_number=apartment_number,
                    created_by=operator_id,
                    comment=comment or f'Создан через кассу при оплате {amount} грн',
                    source='cashier',
                )
                candidate_created = True
                result['candidate_id'] = candidate_id
                result['candidate_created'] = True
        
        # ==========================================
        # 6. Создаём платёж
        # ==========================================
        period = period_code or datetime.now().strftime("%Y-%m")
        
        success, create_result = Payment.create(
            amount=amount,
            apartment_number=apartment_number,  # может быть None
            service_code=service_code,
            payment_method=payment_method,
            vehicle_id=vehicle_id,
            candidate_id=candidate_id,
            comment=comment,
            operator_id=operator_id,
            period_code=period,
        )
        
        if not success:
            result['error'] = f'Ошибка создания платежа: {create_result}'
            return result
        
        payment_id = create_result.get('payment_id')
        result['payment_id'] = payment_id
        
        # ==========================================
        # 7. Если платёж создан и есть кандидат — привязываем
        # ==========================================
        if candidate_id and payment_id:
            DBAdapter.link_payment_to_candidate(payment_id, candidate_id)
        
        result['success'] = True
        result['vehicle_created'] = False
        return result
    
    @staticmethod
    def get_daily_report(date: Optional[str] = None) -> Dict[str, Any]:
        """Получить отчёт по кассе за день (заглушка)"""
        if not date:
            date = datetime.now().strftime('%Y-%m-%d')
        
        return {
            'date': date,
            'total_payments': 0,
            'total_amount': 0.0,
            'cash_payments': 0,
            'card_payments': 0,
            'bank_payments': 0,
            'candidates_count': 0,
            'payments': [],
        }