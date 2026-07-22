import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import sqlite3

OSBB_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(OSBB_ROOT))

from Bots.db_access import (
    get_vehicle_by_id,
    get_apartment_vehicles,
    get_vehicles_by_status,
    get_next_vehicle_for_review,
    update_vehicle_parking_status,
    update_vehicle_plate,
    update_vehicle_model,
    find_apartment,
)


class DBAdapter:
    """Адаптер для работы с БД"""
    
    @staticmethod
    def get_connection() -> sqlite3.Connection:
        """Возвращает соединение с БД"""
        from Bots.db_access import get_conn
        return get_conn()
    
    @staticmethod
    def get_vehicle(vehicle_id: int) -> Optional[Dict[str, Any]]:
        result = get_vehicle_by_id(vehicle_id)
        if not result:
            return None
        return {
            'id': result[0],
            'apartment_number': result[1],
            'plate_norm': result[2],
            'plate_raw': result[3],
            'model_norm': result[4],
            'model_raw': result[5],
            'parking_time': result[6],
        }
    
    @staticmethod
    def get_apartment_vehicles(apartment_number: str) -> List[Dict[str, Any]]:
        vehicles = get_apartment_vehicles(apartment_number)
        return [
            {
                'id': v[0],
                'plate_norm': v[1],
                'plate_raw': v[2],
                'model_norm': v[3],
                'model_raw': v[4],
                'parking_time': v[5],
            }
            for v in vehicles
        ]
    
    @staticmethod
    def get_vehicles_by_status(status: str, limit: int = 50) -> List[Dict[str, Any]]:
        rows = get_vehicles_by_status(status, limit)
        return [
            {
                'id': r[0],
                'apartment_number': r[1],
                'plate_norm': r[2],
                'plate_raw': r[3],
                'model_norm': r[4],
                'model_raw': r[5],
                'parking_time': r[6],
            }
            for r in rows
        ]
    
    @staticmethod
    def get_next_vehicle_for_review() -> Optional[Dict[str, Any]]:
        result = get_next_vehicle_for_review()
        if not result:
            return None
        return {
            'id': result[0],
            'apartment_number': result[1],
            'plate_norm': result[2],
            'plate_raw': result[3],
            'model_norm': result[4],
            'model_raw': result[5],
            'parking_time': result[6],
        }
    
    @staticmethod
    def set_vehicle_parking_status(vehicle_id: int, status: str) -> tuple:
        return update_vehicle_parking_status(vehicle_id, status)
    
    @staticmethod
    def find_apartment(apartment_number: str) -> Optional[Dict[str, Any]]:
        result = find_apartment(apartment_number)
        if not result:
            return None
        return {
            'id': result[0],
            'apartment_number': result[1],
            'entrance': result[2] if len(result) > 2 else None,
        }
    
    # ==========================================
    # ПОИСК АВТОМОБИЛЕЙ
    # ==========================================
    
    @staticmethod
    def find_vehicle_by_plate(plate: str) -> Optional[Dict[str, Any]]:
        """
        Находит автомобиль по номеру (точное совпадение).
        """
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        plate_norm = plate.upper().strip()
        
        cur.execute("""
            SELECT 
                id,
                license_plate_normalized,
                license_plate,
                car_model_normalized,
                car_model,
                parking_time
            FROM vehicles
            WHERE license_plate_normalized = ? OR license_plate = ?
            ORDER BY id
            LIMIT 1
        """, (plate_norm, plate_norm))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            'id': row[0],
            'plate_norm': row[1],
            'plate_raw': row[2],
            'model_norm': row[3],
            'model_raw': row[4],
            'parking_time': row[5],
        }
    
    # ==========================================
    # ЖИТЕЛИ
    # ==========================================
    
    @staticmethod
    def get_resident(user_id: int) -> Optional[Dict[str, Any]]:
        from Bots.db_access import get_resident_account
        result = get_resident_account(user_id)
        if not result:
            return None
        return {
            'id': result[0],
            'telegram_user_id': result[1],
            'telegram_username': result[2],
            'first_name': result[3],
            'last_name': result[4],
            'apartment_id': result[5],
            'apartment_number': result[6],
            'role': result[7],
            'status': result[8],
            'language_code': result[9],
            'created_at': result[10],
            'updated_at': result[11],
            'verified_at': result[12],
            'last_seen_at': result[13],
            'notes': result[14],
        }
    
    @staticmethod
    def get_residents_summary(limit: int = 10) -> Dict[str, Any]:
        from Bots.db_access import get_resident_accounts_summary
        return get_resident_accounts_summary(limit)
    
    @staticmethod
    def get_residents_by_apartment(apartment_number: str) -> List[Dict[str, Any]]:
        from Bots.db_access import get_accounts_by_apartment
        rows = get_accounts_by_apartment(apartment_number)
        return [
            {
                'telegram_user_id': r[0],
                'telegram_username': r[1],
                'first_name': r[2],
                'last_name': r[3],
                'role': r[4],
                'status': r[5],
                'verified_at': r[6],
            }
            for r in rows
        ]
    
    @staticmethod
    def link_apartment(user_id: int, apartment: str) -> tuple:
        from Bots.db_access import link_resident_to_apartment
        return link_resident_to_apartment(user_id, apartment)
    
    @staticmethod
    def unlink_apartment(user_id: int) -> tuple:
        from Bots.db_access import unlink_resident_account
        return unlink_resident_account(user_id)
    
    @staticmethod
    def mark_verified(user_id: int) -> tuple:
        from Bots.db_access import mark_resident_operator_verified
        return mark_resident_operator_verified(user_id)
    
    @staticmethod
    def upsert_resident_from_telegram(user, language_code: str = "ru"):
        from Bots.db_access import upsert_resident_account_from_telegram
        return upsert_resident_account_from_telegram(user, language_code)
    
    # ==========================================
    # КВАРТИРЫ
    # ==========================================
    
    @staticmethod
    def get_apartment_card(apartment_number: str) -> Optional[Dict[str, Any]]:
        from Bots.db_access import get_apartment_card as _get_card
        return _get_card(apartment_number)
    
    # ==========================================
    # VEHICLE CANDIDATES
    # ==========================================
    
    @staticmethod
    def create_vehicle_candidate(
        plate: str,
        plate_norm: str,
        model: Optional[str] = None,
        apartment_number: Optional[str] = None,
        created_by: Optional[int] = None,
        comment: Optional[str] = None,
        source: str = 'cashier',
    ) -> int:
        """Создаёт запись в vehicle_candidates"""
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        apartment_id = None
        if apartment_number:
            apt = DBAdapter.find_apartment(apartment_number)
            if apt:
                apartment_id = apt['id']
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        model_norm = model.upper().strip() if model else None
        
        cur.execute("""
            INSERT INTO vehicle_candidates (
                license_plate,
                license_plate_normalized,
                car_model,
                car_model_normalized,
                apartment_id,
                apartment_number,
                created_by,
                created_at,
                comment,
                source,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
        """, (
            plate,
            plate_norm,
            model,
            model_norm,
            apartment_id,
            apartment_number,
            created_by,
            now,
            comment,
            source,
        ))
        
        candidate_id = cur.lastrowid
        conn.commit()
        conn.close()
        
        return candidate_id
    
    @staticmethod
    def get_vehicle_candidate(candidate_id: int) -> Optional[Dict[str, Any]]:
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT
                id,
                license_plate,
                license_plate_normalized,
                car_model,
                car_model_normalized,
                apartment_id,
                apartment_number,
                status,
                resolved_vehicle_id,
                merged_vehicle_id,
                created_by,
                created_at,
                updated_at,
                comment,
                source
            FROM vehicle_candidates
            WHERE id = ?
        """, (candidate_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            'id': row[0],
            'license_plate': row[1],
            'license_plate_normalized': row[2],
            'car_model': row[3],
            'car_model_normalized': row[4],
            'apartment_id': row[5],
            'apartment_number': row[6],
            'status': row[7],
            'resolved_vehicle_id': row[8],
            'merged_vehicle_id': row[9],
            'created_by': row[10],
            'created_at': row[11],
            'updated_at': row[12],
            'comment': row[13],
            'source': row[14],
        }
    
    @staticmethod
    def find_vehicle_candidate_by_plate(plate: str) -> Optional[Dict[str, Any]]:
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        plate_norm = plate.upper().strip()
        
        cur.execute("""
            SELECT
                id,
                license_plate,
                license_plate_normalized,
                car_model,
                car_model_normalized,
                apartment_id,
                apartment_number,
                status,
                resolved_vehicle_id,
                merged_vehicle_id,
                created_by,
                created_at,
                updated_at,
                comment,
                source
            FROM vehicle_candidates
            WHERE license_plate_normalized = ?
              AND status = 'PENDING'
            ORDER BY id DESC
            LIMIT 1
        """, (plate_norm,))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            'id': row[0],
            'license_plate': row[1],
            'license_plate_normalized': row[2],
            'car_model': row[3],
            'car_model_normalized': row[4],
            'apartment_id': row[5],
            'apartment_number': row[6],
            'status': row[7],
            'resolved_vehicle_id': row[8],
            'merged_vehicle_id': row[9],
            'created_by': row[10],
            'created_at': row[11],
            'updated_at': row[12],
            'comment': row[13],
            'source': row[14],
        }
    
    @staticmethod
    def resolve_vehicle_candidate(candidate_id: int, vehicle_id: int) -> tuple:
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cur.execute("""
            UPDATE vehicle_candidates
            SET
                status = 'RESOLVED',
                resolved_vehicle_id = ?,
                updated_at = ?
            WHERE id = ?
        """, (vehicle_id, now, candidate_id))
        
        changed = cur.rowcount
        conn.commit()
        conn.close()
        
        if changed:
            return True, {'candidate_id': candidate_id, 'vehicle_id': vehicle_id}
        return False, 'candidate_not_found'
    
    @staticmethod
    def update_candidate_status(candidate_id: int, status: str) -> tuple:
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        if status not in ['PENDING', 'RESOLVED', 'REJECTED', 'MERGED']:
            return False, 'invalid_status'
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cur.execute("""
            UPDATE vehicle_candidates
            SET
                status = ?,
                updated_at = ?
            WHERE id = ?
        """, (status, now, candidate_id))
        
        changed = cur.rowcount
        conn.commit()
        conn.close()
        
        if changed:
            return True, {'candidate_id': candidate_id, 'status': status}
        return False, 'candidate_not_found'
    
    @staticmethod
    def link_payment_to_candidate(payment_id: int, candidate_id: int) -> tuple:
        from Bots.db_access import get_conn
        conn = get_conn()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE payments
            SET candidate_id = ?
            WHERE id = ?
        """, (candidate_id, payment_id))
        
        changed = cur.rowcount
        conn.commit()
        conn.close()
        
        if changed:
            return True, {'payment_id': payment_id, 'candidate_id': candidate_id}
        return False, 'payment_not_found'