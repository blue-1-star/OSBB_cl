import pandas as pd
import os
import re

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def normalize_plate(plate):
    plate = str(plate).upper()
    trans_map = {'А': 'A', 'В': 'B', 'Е': 'E', 'І': 'I', 'К': 'K', 
                 'М': 'M', 'Н': 'H', 'О': 'O', 'Р': 'P', 'С': 'C', 'Т': 'T', 'Х': 'X'}
    for cyr, lat in trans_map.items(): plate = plate.replace(cyr, lat)
    return re.sub(r"[^A-Z0-9]", "", plate)

def normalize_apt(apt):
    return re.sub(r'\D', '', str(apt).split('.')[0]).lstrip('0')

def audit_merge_v2():
    base = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx"), dtype=str)
    bot = pd.read_excel(os.path.join(DATA_DIR, "parking_tbot1.xlsx"), dtype=str)
    
    # Нормализуем ключи
    base['apt_norm'] = base['apartment_number'].apply(normalize_apt)
    base['plt_norm'] = base['license_plate'].apply(normalize_plate)
    
    bot['apt_norm'] = bot['Номер квартири'].apply(normalize_apt)
    bot['plt_norm_bot'] = bot['Номер Авто'].apply(normalize_plate)
    
    # Сопоставляем: для каждой записи бота ищем "родной" номер из базы по квартире
    # (просто для визуального сравнения)
    merged = bot.merge(base[['apt_norm', 'plt_norm', 'license_plate']], 
                       on='apt_norm', how='left', suffixes=('_bot', '_base'))
    
    # Сохраняем, чтобы вы видели: [Номер из бота] | [Номер из базы]
    merged.to_excel(os.path.join(DATA_DIR, "Audit_Comparison.xlsx"), index=False)
    print("Аудит готов! Файл Audit_Comparison.xlsx покажет номера рядом.")

if __name__ == "__main__":
    audit_merge_v2()
