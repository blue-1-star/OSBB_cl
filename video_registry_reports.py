"""Shared read-only video plate reports for Streamlit and Telegram."""

from ui_dates import display_date


def short_person_name(full_name):
    """Use the first listed person: 'Прізвище І. П.', never a full patronymic."""
    first = str(full_name or '').split(';', 1)[0].strip()
    parts = first.split()
    if not parts:
        return '—'
    surname = parts[0]
    initials = [f'{part.strip(".")[0]}.' for part in parts[1:3] if part.strip('.')]
    return ' '.join([surname] + initials)


def video_period(conn):
    row = conn.execute('SELECT MIN(first_seen_date),MAX(last_seen_date) FROM video_plate_evidence').fetchone()
    if not row or not row[0]:
        return 'Период съёмок не указан'
    return f'{display_date(row[0])} — {display_date(row[1])}'


def video_registry_rows(conn, *, found):
    """Join by current exact normalized plate, rather than stale import-time links."""
    rows=conn.execute('''SELECT e.plate_normalized,e.observation_count,e.night_count,e.day_count,
        e.report_model,v.id AS vehicle_id,a.apartment_number,
        COALESCE(NULLIF(v.car_model_normalized,''),NULLIF(v.car_model,'')) AS registry_model,
        (SELECT p.full_name FROM persons p WHERE p.apartment_id=a.id
         AND NULLIF(TRIM(p.full_name),'') IS NOT NULL ORDER BY p.id LIMIT 1) AS full_name
        FROM video_plate_evidence e
        LEFT JOIN vehicles v ON v.id=(
            SELECT v2.id FROM vehicles v2
            WHERE v2.license_plate_normalized=e.plate_normalized
            ORDER BY CASE WHEN COALESCE(v2.lifecycle_status,'ACTIVE')='ACTIVE' THEN 0 ELSE 1 END,v2.id
            LIMIT 1)
        LEFT JOIN apartments a ON a.id=v.apartment_id
        WHERE (v.id IS NOT NULL)=?
        ORDER BY e.observation_count DESC,e.plate_normalized''',(1 if found else 0,)).fetchall()
    result=[]
    for row in rows:
        item={
            'Номер':row['plate_normalized'],
            'Количество':int(row['observation_count']),
            'Марка':row['report_model'] or row['registry_model'] or '—',
            'Ночь / День':f"{int(row['night_count'])} / {int(row['day_count'])}",
        }
        if found:
            item={
                'Номер':item['Номер'],'Количество':item['Количество'],
                'Квартира':row['apartment_number'] or '—',
                'ФИО':short_person_name(row['full_name']),
                'Марка':item['Марка'],'Ночь / День':item['Ночь / День'],
            }
        result.append(item)
    return result
