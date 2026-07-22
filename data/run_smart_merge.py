import pandas as pd
import os

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

# Наш «Золотой классификатор»
def classify_tariff(val):
    val = str(val).strip()
    # Night: +, 500
    if val in ['+', '500', '500.0']: 
        return 'Night'
    # Day: -, Гараж
    if val in ['-', 'Гараж']: 
        return 'Day'
    return '' # Пустое оставляем пустым

def run_smart_merge():
    # 1. Загрузка
    base = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx"), dtype=str)
    bot = pd.read_excel(os.path.join(DATA_DIR, "parking_tbot1.xlsx"), dtype=str)
    
    # 2. Ключи
    base['apt_key'] = base['apartment_number'].str.strip()
    bot['apt_key'] = bot['Номер квартири'].str.strip()
    
    # 3. Обработка тарифов из БУМАЖНОЙ базы
    # Предполагаем, что столбец в базе называется 'parking_time'
    if 'parking_time' in base.columns:
        base['norm_tariff'] = base['parking_time'].apply(classify_tariff)
    else:
        base['norm_tariff'] = ''
        
    # 4. Группировка
    base_grouped = base.groupby('apt_key').agg({
        'owner_name': 'first',
        'license_plate': lambda x: ', '.join(sorted(x.dropna().unique())),
        'car_model': lambda x: ', '.join(sorted(x.dropna().unique())),
        'norm_tariff': 'first' 
    }).reset_index()
    
    bot_grouped = bot.groupby('apt_key').agg({
        'ПІБ': 'first',
        'Номер Авто': lambda x: ', '.join(sorted(x.dropna().unique()))
    }).reset_index()
    
    # ПЕРЕНОС: Заполняем тариф авансом
    bot_grouped['tariff_bot'] = base_grouped['norm_tariff']
    
    # 5. Слияние
    final = pd.merge(base_grouped, bot_grouped, on='apt_key', how='outer')
    
    # 6. Сортировка
    def get_sort_key(val):
        digits = ''.join(filter(str.isdigit, val))
        return int(digits) if digits else 99999
    
    final['numeric_sort'] = final['apt_key'].apply(get_sort_key)
    final = final.sort_values('numeric_sort').drop(columns=['numeric_sort'])
    
    final.to_excel(os.path.join(DATA_DIR, "AUDIT_READY_WITH_TARIFFS.xlsx"), index=False)
    print("Файл готов! Тарифы из 'parking_time' бумажной базы успешно нормализованы.")

run_smart_merge()
