import pandas as pd
import os

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def classify_tariff(val):
    val = str(val).strip()
    if val in ['+', '500', '500.0']: return 'Night'
    if val in ['-', 'Гараж']: return 'Day'
    return ''

def run_flat_merge():
    # 1. Загрузка
    base = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx"), dtype=str)
    bot = pd.read_excel(os.path.join(DATA_DIR, "parking_tbot2.xlsx"), dtype=str)
    
    # 2. Нормализация ключей
    base['apt_key'] = base['apartment_number'].str.strip()
    base['plate_key'] = base['license_plate'].str.strip().str.upper()
    bot['apt_key'] = bot['Номер квартири'].str.strip()
    bot['plate_key'] = bot['Номер Авто'].str.strip().str.upper()
    
    # 3. Нормализация тарифов из базы (ищем столбец 'parking_time')
    base['tariff_norm'] = base['parking_time'].apply(classify_tariff)
    
    # 4. Слияние
    final = pd.merge(base, bot, on=['apt_key', 'plate_key'], how='outer')
    
    # 5. Умное заполнение final_tariff
    # Если столбец 'parking_time_y' (из бота) существует, берем его, иначе берем 'tariff_norm'
    if 'parking_time_y' in final.columns:
        final['final_tariff'] = final['parking_time_y'].fillna(final['tariff_norm'])
    else:
        final['final_tariff'] = final['tariff_norm']
        
        # 6. Сортировка - исправленный вариант
    # Принудительно приводим к строке, заменяем 'nan' на '9999' для пустых ячеек
    final['apt_key'] = final['apt_key'].astype(str).replace('nan', '9999')
    
    def get_sort_key(x):
        # Очищаем строку от всего, кроме цифр
        digits = ''.join(filter(str.isdigit, x))
        # Если цифр не нашлось (например, квартира "А"), возвращаем 9999
        return int(digits) if digits else 9999
    
    final['sort_key'] = final['apt_key'].apply(get_sort_key)
    final = final.sort_values('sort_key').drop(columns=['sort_key'])

    
    final.to_excel(os.path.join(DATA_DIR, "FLAT_REGISTRY.xlsx"), index=False)
    print("Файл FLAT_REGISTRY.xlsx успешно создан.")

run_flat_merge()
