"""Read-only, shared inventory of declared OSBB data-quality gaps.

The report is derived from current records on every request. It does not
create verification tasks or treat every nullable technical column as required.
"""

from __future__ import annotations

from collections import Counter
import sqlite3


RULES = {
    "VEHICLE_APARTMENT": ("Автомобиль", "Квартира", "Критично"),
    "VEHICLE_PLATE": ("Автомобиль", "Госномер", "Критично"),
    "VEHICLE_PLATE_SUSPICIOUS": ("Автомобиль", "Госномер требует проверки", "Важно"),
    "VEHICLE_MODEL": ("Автомобиль", "Марка/модель", "Важно"),
    "VEHICLE_PARKING_MODE": ("Автомобиль", "Режим парковки", "Важно"),
    "VEHICLE_COLOR": ("Автомобиль", "Цвет", "Дополнить"),
    "APARTMENT_AREA": ("Квартира", "Площадь", "Важно"),
    "APARTMENT_PERSON": ("Квартира", "ФИО жителя/собственника", "Важно"),
    "APARTMENT_CONTACT": ("Квартира", "Контакт", "Дополнить"),
}


def _empty(value: object) -> bool:
    return value is None or not str(value).strip()


def _apartment_sort(number: str) -> tuple[int, int, str]:
    number = str(number or "")
    return (0, int(number), "") if number.isdigit() else (1, 0, number)


def build_quality_issues(conn: sqlite3.Connection) -> list[dict]:
    """Return one issue per declared rule and source record; no writes."""
    conn.row_factory = sqlite3.Row
    issues: list[dict] = []

    def add(rule: str, table: str, record_id: int, apartment: str, plate: str = "", source: str = "") -> None:
        entity, field, severity = RULES[rule]
        issues.append({
            "rule_code": rule, "entity": entity, "field": field,
            "severity": severity, "object_table": table, "object_id": record_id,
            "apartment": str(apartment or "—"), "plate": plate or "—",
            "source": source or "—",
        })

    apartments = [dict(row) for row in conn.execute("""
        SELECT a.id, a.apartment_number, a.total_area, a.area_sqm,
               a.source, a.unit_type, a.record_status,
               EXISTS(SELECT 1 FROM persons p WHERE p.apartment_id=a.id
                      AND NULLIF(TRIM(p.full_name),'') IS NOT NULL) AS has_person,
               EXISTS(SELECT 1 FROM persons p WHERE p.apartment_id=a.id
                      AND NULLIF(TRIM(p.phone_raw),'') IS NOT NULL) AS has_person_phone,
               EXISTS(SELECT 1 FROM contact_methods c WHERE c.apartment_id=a.id
                      AND NULLIF(TRIM(c.contact_value),'') IS NOT NULL) AS has_contact
        FROM apartments a
        WHERE COALESCE(a.unit_type,'RESIDENTIAL')='RESIDENTIAL'
          AND COALESCE(a.record_status,'')<>'TEST'
    """)]
    for row in apartments:
        number = row["apartment_number"]
        if _empty(row["area_sqm"]) and _empty(row["total_area"]):
            add("APARTMENT_AREA", "apartments", row["id"], number, source=row["source"])
        if not row["has_person"]:
            add("APARTMENT_PERSON", "apartments", row["id"], number, source=row["source"])
        if not row["has_person_phone"] and not row["has_contact"]:
            add("APARTMENT_CONTACT", "apartments", row["id"], number, source=row["source"])

    vehicles = conn.execute("""
        SELECT v.id, v.apartment_id, a.apartment_number,
               v.license_plate, v.license_plate_normalized, v.plate_format_status,
               v.car_model, v.car_model_normalized, v.car_color, v.car_color_normalized,
               v.parking_time, v.source
        FROM vehicles v
        LEFT JOIN apartments a ON a.id=v.apartment_id
        WHERE COALESCE(v.lifecycle_status, UPPER(v.status), 'ACTIVE')<>'ARCHIVED'
          AND COALESCE(a.record_status,'')<>'TEST'
    """)
    for row in vehicles:
        plate = row["license_plate_normalized"] or row["license_plate"] or ""
        apartment = row["apartment_number"] or "—"
        kwargs = {"plate": plate, "source": row["source"]}
        if row["apartment_id"] is None or row["apartment_number"] is None:
            add("VEHICLE_APARTMENT", "vehicles", row["id"], apartment, **kwargs)
        if _empty(plate):
            add("VEHICLE_PLATE", "vehicles", row["id"], apartment, **kwargs)
        elif row["plate_format_status"] == "SUSPICIOUS":
            add("VEHICLE_PLATE_SUSPICIOUS", "vehicles", row["id"], apartment, **kwargs)
        if _empty(row["car_model_normalized"]) and _empty(row["car_model"]):
            add("VEHICLE_MODEL", "vehicles", row["id"], apartment, **kwargs)
        if _empty(row["parking_time"]):
            add("VEHICLE_PARKING_MODE", "vehicles", row["id"], apartment, **kwargs)
        if _empty(row["car_color_normalized"]) and _empty(row["car_color"]):
            add("VEHICLE_COLOR", "vehicles", row["id"], apartment, **kwargs)

    severity_order = {"Критично": 0, "Важно": 1, "Дополнить": 2}
    issues.sort(key=lambda x: (severity_order[x["severity"]], _apartment_sort(x["apartment"]), x["entity"], x["object_id"], x["rule_code"]))
    return issues


def quality_summary(issues: list[dict]) -> dict:
    return {
        "issues": len(issues),
        "records": len({(i["object_table"], i["object_id"]) for i in issues}),
        "by_rule": dict(Counter(i["rule_code"] for i in issues)),
        "by_severity": dict(Counter(i["severity"] for i in issues)),
    }
