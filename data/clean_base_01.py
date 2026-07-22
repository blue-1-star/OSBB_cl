import pandas as pd
import re
import os
from datetime import datetime

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def classify_tariff(val):
    val = str(val).strip()
    if val in ['+', '500', '500.0']: return 'Night'
    if val in ['-', 'Гараж']: return 'Day'
    return ''

def clean_base():
    df = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx"), dtype=str)
    cleaned_rows = []

    for _, row in df.iterrows():
        apt_str = str(row['apartment_number']).strip()
        plate = str(row['license_plate']).strip().upper()
        
        # 1. Парсим квартиры
        if apt_str == "89.9": 
            apts = ["89", "90"]
        else:
            apts = re.split(r'[./]', apt_str)
        
        # 2. Если квартир несколько, а авто одно (или несколько)
        # Мы НЕ дублируем каждое авто на каждую квартиру, 
        # а создаем запись только для тех связок, которые логичны.
        # В вашем случае "31.32" + "Audi" должно стать 
        # 31 | Audi И 32 | Audi - ЭТО ПРАВИЛЬНО.
        
        for apt in apts:
            new_row = row.copy()
            new_row['apartment_number'] = apt.strip()
            new_row['norm_tariff'] = classify_tariff(row.get('parking_time', ''))
            cleaned_rows.append(new_row)

    new_df = pd.DataFrame(cleaned_rows)
    
    # 3. КЛЮЧЕВОЙ МОМЕНТ: Удаляем полные дубликаты
    # Если у вас 31+Audi и 32+Audi повторяются 2 раза, останется только 1 уникальная запись
    new_df = new_df.drop_duplicates(subset=['apartment_number', 'license_plate'])
    
    # 4. Сохранение
    date_str = datetime.now().strftime("%d_%m")
    output_name = f"OSBB_Base_Cleaned_{date_str}.xlsx"
    new_df.to_excel(os.path.join(DATA_DIR, output_name), index=False)
    
    print(f"База очищена. Уникальных записей: {len(new_df)}")
    print(f"Новый эталон: {output_name}")

if __name__ == "__main__":
    clean_base()