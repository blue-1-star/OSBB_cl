"""
Каталог услуг — динамический справочник, читаемый из БД.
Структура соответствует реальной таблице service_catalog.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
import sqlite3

from ..adapters.db_adapter import DBAdapter


class ServiceCategory(str, Enum):
    PARKING = "PARKING"
    ACCESS = "ACCESS"
    IMPROVEMENT = "IMPROVEMENT"
    EQUIPMENT = "EQUIPMENT"
    BARRIER = "BARRIER"
    PARKING_ASSET = "PARKING_ASSET"
    COMMERCIAL = "COMMERCIAL"        # ← ДОБАВЛЕНО
    FUNDRAISING = "FUNDRAISING"      # ← ДОБАВЛЕНО


class ServiceType(str, Enum):
    MONTHLY = "MONTHLY"
    ONE_TIME = "ONE_TIME"
    FUNDRAISING = "FUNDRAISING"
    COMMERCIAL = "COMMERCIAL"


@dataclass
class Service:
    """Модель услуги из каталога (соответствует структуре БД)"""
    code: str
    name: str
    group: str
    category: Optional[ServiceCategory]
    service_type: Optional[ServiceType]
    is_active: bool = True
    is_monthly: bool = False
    is_fundraising: bool = False
    is_commercial: bool = False
    is_access_control: bool = False
    is_cash_collectable: bool = True
    comment: str = ""
    access_policy_enabled: bool = False
    access_policy_scope: str = "NONE"
    access_policy_mode: str = "NONE"
    access_policy_message: str = ""
    manual_review_required: bool = False
    policy_updated_at: Optional[str] = None
    policy_updated_by: Optional[str] = None
    
    @classmethod
    def from_db_row(cls, row: dict) -> 'Service':
        """Создает Service из строки БД (словарь)"""
        category_value = row.get('category')
        category = None
        if category_value:
            try:
                category = ServiceCategory(category_value)
            except ValueError:
                pass  # игнорируем неизвестные категории
        
        service_type_value = row.get('service_type')
        service_type = None
        if service_type_value:
            try:
                service_type = ServiceType(service_type_value)
            except ValueError:
                pass
        
        return cls(
            code=row.get('service_code'),
            name=row.get('service_name'),
            group=row.get('service_group'),
            category=category,
            service_type=service_type,
            is_active=bool(row.get('is_active', 1)),
            is_monthly=bool(row.get('is_monthly', 0)),
            is_fundraising=bool(row.get('is_fundraising', 0)),
            is_commercial=bool(row.get('is_commercial', 0)),
            is_access_control=bool(row.get('is_access_control', 0)),
            is_cash_collectable=bool(row.get('is_cash_collectable', 1)),
            comment=row.get('comment', ''),
            access_policy_enabled=bool(row.get('access_policy_enabled', 0)),
            access_policy_scope=row.get('access_policy_scope', 'NONE'),
            access_policy_mode=row.get('access_policy_mode', 'NONE'),
            access_policy_message=row.get('access_policy_message', ''),
            manual_review_required=bool(row.get('manual_review_required', 0)),
            policy_updated_at=row.get('policy_updated_at'),
            policy_updated_by=row.get('policy_updated_by'),
        )


class ServiceCatalog:
    """
    Единый справочник услуг.
    Загружается из БД при первом обращении и кэшируется.
    """
    
    _cache: Optional[Dict[str, Service]] = None
    
    @classmethod
    def _load_from_db(cls) -> Dict[str, Service]:
        """Загружает каталог из БД (только активные услуги)"""
        result = {}
        
        try:
            conn = DBAdapter.get_connection()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 
                    service_code,
                    service_name,
                    service_group,
                    service_type,
                    category,
                    is_active,
                    is_monthly,
                    is_fundraising,
                    is_commercial,
                    is_access_control,
                    is_cash_collectable,
                    comment,
                    access_policy_enabled,
                    access_policy_scope,
                    access_policy_mode,
                    access_policy_message,
                    manual_review_required,
                    policy_updated_at,
                    policy_updated_by
                FROM service_catalog
                WHERE is_active = 1
                ORDER BY service_code
            """)
            
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            
            for row in rows:
                row_dict = dict(zip(columns, row))
                service = Service.from_db_row(row_dict)
                result[service.code] = service
            
            conn.close()
            
        except Exception as e:
            print(f"⚠️ Ошибка загрузки каталога услуг из БД: {e}")
            return {}
        
        return result
    
    @classmethod
    def _get_cache(cls) -> Dict[str, Service]:
        """Возвращает кэш, загружая его при первом вызове"""
        if cls._cache is None:
            cls._cache = cls._load_from_db()
        return cls._cache
    
    @classmethod
    def get(cls, code: str) -> Optional[Service]:
        """Получить услугу по коду"""
        return cls._get_cache().get(code)
    
    @classmethod
    def get_name(cls, code: str) -> str:
        """Получить название услуги по коду"""
        service = cls.get(code)
        return service.name if service else code
    
    @classmethod
    def get_all(cls) -> List[Service]:
        """Получить все услуги"""
        return list(cls._get_cache().values())
    
    @classmethod
    def get_by_category(cls, category: ServiceCategory) -> List[Service]:
        """Получить услуги по категории"""
        return [s for s in cls.get_all() if s.category == category]
    
    @classmethod
    def get_parking_services(cls) -> List[Service]:
        """Получить все услуги парковки"""
        return cls.get_by_category(ServiceCategory.PARKING)
    
    @classmethod
    def is_valid(cls, code: str) -> bool:
        """Проверить, существует ли услуга"""
        return code in cls._get_cache()
    
    @classmethod
    def refresh(cls) -> None:
        """Принудительно обновить кэш (после изменения БД)"""
        cls._cache = None
        cls._get_cache()