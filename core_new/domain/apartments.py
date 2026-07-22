"""
Доменная модель: Квартиры (Apartment)
Объединяет жителей, автомобили и статусы
"""

from typing import Optional, List, Dict, Any
from ..adapters.db_adapter import DBAdapter


class Apartment:
    """
    Модель квартиры в новой архитектуре.
    
    Объединяет:
    - Информацию о квартире (номер, подъезд)
    - Жителей (Resident)
    - Автомобили (Vehicle)
    - Статус согласования (Verification)
    """
    
    def __init__(self, data: Dict[str, Any]):
        """
        Создает объект Apartment из данных
        
        Args:
            data: словарь с полями от DBAdapter
        """
        self._data = data
        
        # Основные поля
        self.id = data.get('id')
        self.apartment_number = data.get('apartment_number')
        self.entrance = data.get('entrance')
        
        # Дополнительные поля (из карточки)
        self._residents = data.get('residents', [])
        self._vehicles = data.get('vehicles', [])
        self._verification = data.get('verification')
    
    # ==========================================
    # СВОЙСТВА
    # ==========================================
    
    @property
    def display_name(self) -> str:
        """Отображение квартиры"""
        entrance_part = f" (п. {self.entrance})" if self.entrance else ""
        return f"🏠 {self.apartment_number}{entrance_part}"
    
    @property
    def residents(self) -> List:
        """Получить жителей квартиры (ленивая загрузка)"""
        if not self._residents:
            from .residents import Resident
            self._residents = Resident.get_by_apartment(self.apartment_number)
        return self._residents
    
    @property
    def vehicles(self) -> List:
        """Получить автомобили квартиры (ленивая загрузка)"""
        if not self._vehicles:
            from .vehicles import Vehicle
            self._vehicles = Vehicle.get_by_apartment(self.apartment_number)
        return self._vehicles
    
    @property
    def residents_count(self) -> int:
        """Количество жителей"""
        return len(self.residents)
    
    @property
    def vehicles_count(self) -> int:
        """Количество автомобилей"""
        return len(self.vehicles)
    
    @property
    def has_residents(self) -> bool:
        """Есть ли жители"""
        return self.residents_count > 0
    
    @property
    def has_vehicles(self) -> bool:
        """Есть ли автомобили"""
        return self.vehicles_count > 0
    
    @property
    def verification_status(self) -> Optional[str]:
        """Статус согласования квартиры"""
        if self._verification:
            return self._verification.get('status')
        return None
    
    @property
    def verification_status_display(self) -> str:
        """Статус согласования для отображения"""
        status_map = {
            'new': '🆕 Не согласована',
            'confirmed': '✅ Согласована',
            'deferred': '⏳ Отложена',
            'conflict': '⚠️ Конфликт',
            'in_progress': '🔄 В работе',
        }
        status = self.verification_status
        return status_map.get(status, f'❓ {status}') if status else '❓ Неизвестно'
    
    # ==========================================
    # МЕТОДЫ
    # ==========================================
    
    def get_residents_list(self) -> List[Dict[str, str]]:
        """Получить список жителей для отображения"""
        result = []
        for r in self.residents:
            if hasattr(r, 'display_name'):
                result.append({
                    'name': r.display_name,
                    'username': r.username_display,
                    'status': r.status_display,
                })
            elif isinstance(r, (tuple, list)):
                first_name = r[0] if len(r) > 0 else ''
                last_name = r[1] if len(r) > 1 else ''
                username = r[2] if len(r) > 2 else ''
                status = r[3] if len(r) > 3 else ''
                result.append({
                    'name': " ".join(p for p in [first_name, last_name] if p) or "Неизвестный",
                    'username': f"@{username}" if username else "-",
                    'status': status or '-',
                })
        return result
    
    def get_vehicles_list(self) -> List[Dict[str, str]]:
        """Получить список автомобилей для отображения"""
        result = []
        for v in self.vehicles:
            if hasattr(v, 'display_name'):
                result.append({
                    'plate': v.plate,
                    'model': v.model,
                    'status': v.status_display,
                })
            elif isinstance(v, (tuple, list)):
                plate = v[1] or v[2] or '-'
                model = v[3] or v[4] or '-'
                parking_time = v[5] if len(v) > 5 else None
                if parking_time == 'Day':
                    status = "☀️ День"
                elif parking_time == 'Night':
                    status = "🌙 Ночь"
                elif parking_time == 'Inactive':
                    status = "🚫 Не паркуется"
                else:
                    status = "❓ Не указан"
                result.append({
                    'plate': plate,
                    'model': model,
                    'status': status,
                })
        return result
    
    def get_summary(self) -> Dict[str, Any]:
        """Получить сводку по квартире"""
        return {
            'apartment_number': self.apartment_number,
            'entrance': self.entrance,
            'residents_count': self.residents_count,
            'vehicles_count': self.vehicles_count,
            'verification_status': self.verification_status_display,
            'has_residents': self.has_residents,
            'has_vehicles': self.has_vehicles,
        }
    
    def format_card(self) -> str:
        """
        Сформировать карточку квартиры для отображения
        
        Returns:
            строка с информацией о квартире
        """
        lines = []
        lines.append(f"{self.display_name}")
        lines.append("")
        
        # Статус согласования
        lines.append(f"Статус: {self.verification_status_display}")
        lines.append("")
        
        # Жители
        lines.append(f"👥 Жильцы ({self.residents_count}):")
        if self.has_residents:
            for r in self.residents:
                # Проверяем, является ли r объектом Resident или кортежем
                if hasattr(r, 'display_name'):
                    # Это объект Resident
                    lines.append(f"  • {r.display_name} | {r.username_display}")
                elif isinstance(r, (tuple, list)):
                    # Это кортеж из БД: (first_name, last_name, username, status)
                    first_name = r[0] if len(r) > 0 else ''
                    last_name = r[1] if len(r) > 1 else ''
                    username = r[2] if len(r) > 2 else ''
                    status = r[3] if len(r) > 3 else ''
                    name = " ".join(p for p in [first_name, last_name] if p) or "Неизвестный"
                    username_display = f"@{username}" if username else "-"
                    lines.append(f"  • {name} | {username_display} | {status or '-'}")
                else:
                    lines.append(f"  • {r}")
        else:
            lines.append("  нет жильцов")
        
        lines.append("")
        
        # Автомобили
        lines.append(f"🚗 Автомобили ({self.vehicles_count}):")
        if self.has_vehicles:
            for v in self.vehicles:
                # Проверяем, является ли v объектом Vehicle или кортежем
                if hasattr(v, 'display_name'):
                    # Это объект Vehicle
                    lines.append(f"  • {v.plate} | {v.model} | {v.status_display}")
                elif isinstance(v, (tuple, list)):
                    # Это кортеж из БД: (id, plate_norm, plate_raw, model_norm, model_raw, parking_time)
                    plate = v[1] or v[2] or '-'
                    model = v[3] or v[4] or '-'
                    parking_time = v[5] if len(v) > 5 else None
                    if parking_time == 'Day':
                        status = "☀️ День"
                    elif parking_time == 'Night':
                        status = "🌙 Ночь"
                    elif parking_time == 'Inactive':
                        status = "🚫 Не паркуется"
                    else:
                        status = "❓ Не указан"
                    lines.append(f"  • {plate} | {model} | {status}")
                else:
                    lines.append(f"  • {v}")
        else:
            lines.append("  нет автомобилей")
        
        return "\n".join(lines)
    
    # ==========================================
    # ФАБРИЧНЫЕ МЕТОДЫ
    # ==========================================
    
    @classmethod
    def get_by_number(cls, apartment_number: str) -> Optional['Apartment']:
        """
        Получить квартиру по номеру
        
        Args:
            apartment_number: номер квартиры
        """
        data = DBAdapter.get_apartment_card(apartment_number)
        if not data:
            return None
        
        # Трансформируем данные в нужный формат
        apartment_data = data.get('apartment', [None, None, None])
        return cls({
            'id': apartment_data[0] if apartment_data else None,
            'apartment_number': data.get('apartment_number'),
            'entrance': apartment_data[2] if apartment_data and len(apartment_data) > 2 else None,
            'residents': data.get('residents', []),
            'vehicles': data.get('vehicles', []),
            'verification': data.get('verification'),
        })
    
    @classmethod
    def get_all(cls, limit: int = 50) -> List['Apartment']:
        """
        Получить все квартиры
        
        Args:
            limit: максимальное количество
        """
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT apartment_number, entrance
            FROM apartments
            ORDER BY apartment_number
            LIMIT ?
        """, (limit,))
        
        rows = cur.fetchall()
        conn.close()
        
        apartments = []
        for row in rows:
            apartments.append(cls({
                'apartment_number': row[0],
                'entrance': row[1] if len(row) > 1 else None,
            }))
        
        return apartments
    
    @classmethod
    def get_by_entrance(cls, entrance: str) -> List['Apartment']:
        """Получить все квартиры подъезда"""
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT apartment_number, entrance
            FROM apartments
            WHERE entrance = ?
            ORDER BY apartment_number
        """, (entrance,))
        
        rows = cur.fetchall()
        conn.close()
        
        return [
            cls({
                'apartment_number': row[0],
                'entrance': row[1],
            })
            for row in rows
        ]


class ApartmentCandidate:
    """
    Кандидат квартиры.
    
    Новая сущность для случаев, когда квартира ещё не создана в БД,
    но появляется в источниках (telegram, tbot).
    """
    
    def __init__(self, apartment_number: str, entrance: Optional[str] = None):
        self.apartment_number = apartment_number
        self.entrance = entrance
        self.status = 'pending'  # pending | created | duplicate
        self.source = None
        self.notes = []
    
    @property
    def display_name(self) -> str:
        entrance_part = f" (п. {self.entrance})" if self.entrance else ""
        return f"{self.apartment_number}{entrance_part}"
    
    def create(self) -> tuple:
        """
        Создать квартиру в БД
        
        Returns:
            (success, result)
        """
        # Проверяем, есть ли уже
        existing = Apartment.get_by_number(self.apartment_number)
        if existing:
            self.status = 'duplicate'
            return False, f"Квартира {self.apartment_number} уже существует"
        
        # Создаем через старый код
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        try:
            cur.execute("""
                INSERT INTO apartments (apartment_number, entrance)
                VALUES (?, ?)
            """, (self.apartment_number, self.entrance))
            
            conn.commit()
            self.status = 'created'
            return True, Apartment.get_by_number(self.apartment_number)
            
        except Exception as e:
            conn.rollback()
            return False, f"Ошибка: {e}"
        finally:
            conn.close()
    
    def add_note(self, note: str):
        """Добавить заметку"""
        self.notes.append(note)