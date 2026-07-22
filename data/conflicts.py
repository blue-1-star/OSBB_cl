import pandas as pd
import os
import re

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def normalize_plate(plate):
    plate = str(plate).upper()
    # Добавили все возможные варианты замены для 'Є' и других
    trans_map = {'А': 'A', 'В': 'B', 'Е': 'E', 'І': 'I', 'К': 'K', 
                 'М': 'M', 'Н': 'H', 'О': 'O', 'Р': 'P', 'С': 'C', 
                 'Т': 'T', 'Х': 'X', 'Є': 'E'}
    for cyr, lat in trans_map.items(): 
        plate = plate.replace(cyr, lat)
    return re.sub(r"[^A-Z0-9]", "", plate)

def normalize_apt(apt):
    return re.sub(r'\D', '', str(apt).split('.')[0]).lstrip('0')

def find_real_conflicts():
    base = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx"), dtype=str)
    bot = pd.read_excel(os.path.join(DATA_DIR, "parking_tbot1.xlsx"), dtype=str)
    
    COL_MAPPING = {'Номер квартири': 'apartment_number', 'Номер Авто': 'license_plate'}
    bot = bot.rename(columns=COL_MAPPING)
    
    # ВАЖНО: Нормализуем обе стороны одинаково
    base['key'] = base['apartment_number'].apply(normalize_apt) + "_" + base['license_plate'].apply(normalize_plate)
    bot['key'] = bot['apartment_number'].apply(normalize_apt) + "_" + bot['license_plate'].apply(normalize_plate)
    
    # Теперь сравнение идет по "чистым" ключам
    conflicts = bot[~bot['key'].isin(base['key'])].copy()
    
    conflicts.to_excel(os.path.join(DATA_DIR, "REAL_CONFLICTS_ONLY.xlsx"), index=False)
    print(f"Готово. Записей в конфликтах: {len(conflicts)}")

if __name__ == "__main__":
    find_real_conflicts()
