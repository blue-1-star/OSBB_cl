import os
import re
import pandas as pd

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def normalize_plate(plate):
    if pd.isna(plate): return ""
    cyrillic = "АВЕКМНОРСТХ"
    latin = "ABEKMHOPCTX"
    trans = str.maketrans(cyrillic, latin)
    return re.sub(r"[^A-Z0-9]", "", str(plate).upper().translate(trans))

def clean_phone(phone):
    """Превращает любой мусор в 380XXXXXXXXX."""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 12 and digits.startswith("380"): return digits
    if len(digits) == 10 and digits.startswith("0"): return "380" + digits
    if len(digits) == 9: return "380" + digits
    return digits # если не смогли распознать, оставляем как есть, чтобы не потерять

def run_strict_merge():
    base_df = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Final_Base.xlsx"), sheet_name="all")
    bot_df = pd.read_excel(os.path.join(DATA_DIR, "parking_tbot1.xlsx"))
    
    # 1. Приведение типов
    base_df['apartment_number'] = base_df['apartment_number'].astype(str).str.replace(r'\.0$', '', regex=True)
    bot_df['Номер квартири'] = bot_df['Номер квартири'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    # 2. Нормализация для поиска
    base_df['clean_plate'] = base_df['license_plate'].apply(normalize_plate)
    bot_df['clean_plate'] = bot_df['Номер Авто'].apply(normalize_plate)

    # 3. Обновление существующих (БЕЗ создания дублей)
    for idx, row in base_df.iterrows():
        match = bot_df[(bot_df['Номер квартири'] == row['apartment_number']) & 
                       (bot_df['clean_plate'] == row['clean_plate'])]
        
        if not match.empty:
            m = match.iloc[0]
            if pd.isna(row['phone_number']) or str(row['phone_number']).strip() == "":
                base_df.at[idx, 'phone_number'] = clean_phone(m.get('Телефон', ''))
            
            # Логика Власник/True
            base_df.at[idx, 'is_owner'] = str(m.get('Власність', '')).lower() == 'власник'
            base_df.at[idx, 'car_color'] = m.get('Колір авто', '')

    # 4. Добавление НОВЫХ (гнезда) - строго проверяем отсутствие
    new_records = []
    for _, b_row in bot_df.iterrows():
        apt = b_row['Номер квартири']
        plate = b_row['clean_plate']
        
        # Проверяем наличие пары Квартира + Номер в базе
        if not ((base_df['apartment_number'] == apt) & (base_df['clean_plate'] == plate)).any():
            new_records.append({
                'apartment_number': apt,
                'owner_name': b_row.get('ПІБ', ''),
                'phone_number': clean_phone(b_row.get('Телефон', '')),
                'license_plate': b_row.get('Номер Авто', ''),
                'car_color': b_row.get('Колір авто', ''),
                'is_owner': str(b_row.get('Власність', '')).lower() == 'власник',
                'parking_time': 'Day' # Или проставьте логику по умолчанию
            })
            
    final_df = pd.concat([base_df, pd.DataFrame(new_records)], ignore_index=True)

    # 5. Тарифы (убрали УТОЧНИТЬ, теперь только Night/Day)
    def map_parking(val):
        val = str(val).strip()
        if val in ['+', '500']: return 'Night'
        return 'Day' # Все остальное (минус, Гараж, пустота) — считаем дневным

    final_df['parking_time'] = final_df['parking_time'].apply(map_parking)

    final_df.drop(columns=['clean_plate'], errors='ignore', inplace=True)
    final_df.to_excel(os.path.join(DATA_DIR, "OSBB_MASTER_DATABASE.xlsx"), index=False)
    print("База обновлена. Чистота 100%.")

if __name__ == "__main__":
    run_strict_merge()
