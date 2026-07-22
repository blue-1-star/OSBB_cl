"""
Доменная модель: Жители (Resident)
Связывает Telegram-пользователей с квартирами и данными
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from ..adapters.db_adapter import DBAdapter


class Resident:
    """
    Модель жителя (пользователя Telegram) в новой архитектуре.
    
    Связывает:
    - Telegram-пользователя
    - Квартиру (если привязана)
    - Статус верификации
    - Роль
    """
    
    def __init__(self, data: Dict[str, Any]):
        """
        Создает объект Resident из данных
        
        Args:
            data: словарь с полями от DBAdapter
        """
        self._data = data
        
        # Основные поля
        self.id = data.get('id')
        self.telegram_user_id = data.get('telegram_user_id')
        self.telegram_username = data.get('telegram_username')
        self.first_name = data.get('first_name')
        self.last_name = data.get('last_name')
        
        # Квартира
        self.apartment_id = data.get('apartment_id')
        self.apartment_number = data.get('apartment_number')
        
        # Статусы и роли
        self.role = data.get('role', 'resident')
        self.status = data.get('status', 'new')
        self.language_code = data.get('language_code', 'ru')
        
        # Даты
        self.created_at = data.get('created_at')
        self.updated_at = data.get('updated_at')
        self.verified_at = data.get('verified_at')
        self.last_seen_at = data.get('last_seen_at')
        
        # Дополнительно
        self.notes = data.get('notes')
    
    # ==========================================
    # СВОЙСТВА (удобные геттеры)
    # ==========================================
    
    @property
    def display_name(self) -> str:
        """Полное имя для отображения"""
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p) or "Неизвестный"
    
    @property
    def username_display(self) -> str:
        """Username для отображения"""
        return f"@{self.telegram_username}" if self.telegram_username else "-"
    
    @property
    def has_apartment(self) -> bool:
        """Привязана ли квартира"""
        return bool(self.apartment_number)
    
    @property
    def is_operator_verified(self) -> bool:
        """Проверен ли оператором"""
        return self.status == 'operator_verified'
    
    @property
    def is_self_confirmed(self) -> bool:
        """Подтвердил ли квартиру самостоятельно"""
        return self.status == 'apartment_confirmed'
    
    @property
    def status_display(self) -> str:
        """Статус для отображения"""
        status_map = {
            'new': '🆕 Новый',
            'apartment_confirmed': '✅ Подтвердил квартиру',
            'operator_verified': '✅ Проверен оператором',
            'blocked': '🚫 Заблокирован',
        }
        return status_map.get(self.status, f"⚠️ {self.status}")
    
    @property
    def role_display(self) -> str:
        """Роль для отображения"""
        role_map = {
            'resident': '👤 Житель',
            'admin': '🔐 Администратор',
            'super_admin': '⭐ Супер-админ',
            'guard': '🛡️ Охрана',
        }
        return role_map.get(self.role, self.role)
    
    # ==========================================
    # МЕТОДЫ (действия)
    # ==========================================
    
    def link_apartment(self, apartment_number: str) -> tuple:
        """
        Привязать квартиру к жителю
        
        Args:
            apartment_number: номер квартиры
        
        Returns:
            (success, result)
        """
        ok, msg = DBAdapter.link_apartment(self.telegram_user_id, apartment_number)
        
        if ok:
            self.apartment_number = apartment_number
            self.status = 'apartment_confirmed'
        
        return ok, msg
    
    def unlink_apartment(self) -> tuple:
        """Отвязать квартиру"""
        ok, msg = DBAdapter.unlink_apartment(self.telegram_user_id)
        
        if ok:
            self.apartment_number = None
            self.apartment_id = None
            self.status = 'new'
        
        return ok, msg
    
    def mark_verified_by_operator(self) -> tuple:
        """Отметить как проверенного оператором"""
        ok, msg = DBAdapter.mark_verified(self.telegram_user_id)
        
        if ok:
            self.status = 'operator_verified'
            self.verified_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        return ok, msg
    
    def get_vehicles(self) -> List:
        """Получить автомобили жителя (через квартиру)"""
        if not self.has_apartment:
            return []
        
        from .vehicles import Vehicle
        return Vehicle.get_by_apartment(self.apartment_number)
    
    # ==========================================
    # ФАБРИЧНЫЕ МЕТОДЫ (получение из БД)
    # ==========================================
    
    @classmethod
    def get_by_telegram_id(cls, user_id: int) -> Optional['Resident']:
        """
        Получить жителя по Telegram ID
        
        Args:
            user_id: Telegram ID пользователя
        """
        data = DBAdapter.get_resident(user_id)
        if not data:
            return None
        return cls(data)
    
    @classmethod
    def get_by_apartment(cls, apartment_number: str) -> List['Resident']:
        """
        Получить всех жителей квартиры
        
        Args:
            apartment_number: номер квартиры
        """
        data_list = DBAdapter.get_residents_by_apartment(apartment_number)
        return [cls(data) for data in data_list]
    
    @classmethod
    def get_all(cls, limit: int = 30) -> List['Resident']:
        """
        Получить всех жителей (сводка)
        
        Args:
            limit: максимальное количество
        """
        summary = DBAdapter.get_residents_summary(limit)
        # summary возвращает словарь с ключом 'rows'
        rows = summary.get('rows', [])
        return [cls({
            'telegram_user_id': r[0],
            'telegram_username': r[1],
            'first_name': r[2],
            'last_name': r[3],
            'apartment_number': r[4],
            'status': r[5],
            'last_seen_at': r[6],
        }) for r in rows]
    
    # ==========================================
    # СТАТИЧЕСКИЕ МЕТОДЫ (поиск и создание)
    # ==========================================
    
    @staticmethod
    def create_from_telegram(user, language_code: str = "ru") -> 'Resident':
        """
        Создать/обновить жителя из Telegram-объекта
        
        Args:
            user: объект Telegram User
            language_code: код языка
        """
        DBAdapter.upsert_resident_from_telegram(user, language_code)
        return Resident.get_by_telegram_id(user.id)
    
    @staticmethod
    def search(query: str) -> List['Resident']:
        """
        Поиск жителей по имени или username
        
        Args:
            query: строка поиска
        """
        # Используем существующий фильтр
        # (можно расширить)
        pass


class ResidentCandidate:
    """
    Кандидат жителя.
    
    Новая сущность для случаев, когда житель ещё не подтвержден.
    """
    
    def __init__(self, telegram_user_id: int, first_name: str, last_name: str = None):
        self.telegram_user_id = telegram_user_id
        self.first_name = first_name
        self.last_name = last_name
        self.status = 'pending'
        self.notes = []
    
    @property
    def display_name(self) -> str:
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p) or "Неизвестный"
    
    def confirm(self, apartment_number: str) -> tuple:
        """
        Подтвердить жителя и привязать квартиру
        
        Args:
            apartment_number: номер квартиры
        """
        ok, msg = DBAdapter.link_apartment(self.telegram_user_id, apartment_number)
        if ok:
            self.status = 'confirmed'
        return ok, msg