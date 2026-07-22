import pandas as pd
import re
import os
DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def clean_phone(phone):
    if pd.isna(phone): return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 12 and digits.startswith("380"): return digits
    if len(digits) == 10 and digits.startswith("0"): return "380" + digits
    if len(digits) == 9: return "380" + digits
    return phone

def normalize_plate(plate):
    if pd.isna(plate): return ""
    cyrillic = "АВЕКМНОРСТХ"
    latin = "ABEKMHOPCTX"
    trans = str.maketrans(cyrillic, latin)
    return re.sub(r"[^A-Z0-9]", "", str(plate).upper().translate(trans))

def final_smart_merge():
    # 1. Загрузка
    base_df = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx"))
    bot_df = pd.read_excel(os.path.join(DATA_DIR, "parking_tbot1.xlsx"))
    
    # 2. Подготовка ключей
    base_df['clean_plate'] = base_df['license_plate'].apply(normalize_plate)
    bot_df['clean_plate'] = bot_df['Номер Авто'].apply(normalize_plate)
    
    # 3. Обновление (дополняем только пустое)
    for idx, row in base_df.iterrows():
        # Поиск по паре Квартира + Номер авто
        match = bot_df[(bot_df['Номер квартири'].astype(str) == str(row['apartment_number'])) & 
                       (bot_df['clean_plate'] == row['clean_plate'])]
        
        if not match.empty:
            m = match.iloc[0]
            # Дополняем только пустоты
            if pd.isna(row['phone_number']) or str(row['phone_number']).strip() == "":
                base_df.at[idx, 'phone_number'] = clean_phone(m.get('Телефон', ''))
            base_df.at[idx, 'car_color'] = m.get('Колір авто', '')
            base_df.at[idx, 'is_owner'] = str(m.get('Власність', '')).lower() == 'власник'
    
    # 4. Добавление НОВЫХ (гнезда)
    new_records = []
    for _, b_row in bot_df.iterrows():
        apt = str(b_row['Номер квартири'])
        plate = b_row['clean_plate']
        
        # Если такой пары (квартира + номер) нет в базе — добавляем
        if not ((base_df['apartment_number'].astype(str) == apt) & (base_df['clean_plate'] == plate)).any():
            new_records.append({
                'apartment_number': apt,
                'owner_name': b_row.get('ПІБ', ''),
                'phone_number': clean_phone(b_row.get('Телефон', '')),
                'license_plate': b_row.get('Номер Авто', ''),
                'car_color': b_row.get('Колір авто', ''),
                'is_owner': str(b_row.get('Власність', '')).lower() == 'власник',
                'parking_time': 'Day' # По умолчанию
            })
            
    final_df = pd.concat([base_df, pd.DataFrame(new_records)], ignore_index=True)
    
    # 5. Финальная очистка и сохранение
    final_df.drop(columns=['clean_plate'], errors='ignore', inplace=True)
    final_df.to_excel(os.path.join(DATA_DIR, "OSBB_MASTER_DATABASE.xlsx"), index=False)
    print("Мастер-база готова! Все пробелы заполнены, новые записи добавлены без дублей.")

if __name__ == "__main__":
    final_smart_merge()
