import pandas as pd
import os

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def classify_tariff(val):
    # Приводим к строке, убираем пробелы
    val = str(val).strip()
    # Night: +, 500
    if val in ['+', '500', '500.0']: return 'Night'
    # Day: -, Гараж
    if val in ['-', 'Гараж']: return 'Day'
    return ''

def run_flat_merge():
    # 1. Загрузка с принудительным строковым типом
    base = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx"), dtype=str)
    bot = pd.read_excel(os.path.join(DATA_DIR, "parking_tbot2.xlsx"), dtype=str)
    
    # 2. Очистка ключей от пустых значений и пробелов
    base['apt_key'] = base['apartment_number'].fillna('').astype(str).str.strip()
    base['plate_key'] = base['license_plate'].fillna('').astype(str).str.strip().str.upper()
    
    bot['apt_key'] = bot['Номер квартири'].fillna('').astype(str).str.strip()
    bot['plate_key'] = bot['Номер Авто'].fillna('').astype(str).str.strip().str.upper()
    
    # 3. Нормализация тарифов из базы (если столбец parking_time существует)
    if 'parking_time' in base.columns:
        base['tariff_norm'] = base['parking_time'].apply(classify_tariff)
    else:
        base['tariff_norm'] = ''
        
    # 4. Слияние (Outer join сохраняет все записи)
    final = pd.merge(base, bot, on=['apt_key', 'plate_key'], how='outer')
    
    # 5. ГАРАНТИЯ: Убираем любые float-типы и NaN, возникшие после merge
    final = final.fillna('')
    final['apt_key'] = final['apt_key'].astype(str)
    
    # 6. Сортировка с защитой от ошибок
    def get_sort_key(x):
        # Очищаем от мусора и оставляем только цифры
        digits = ''.join(filter(str.isdigit, x))
        # Возвращаем число для сортировки (или 9999, если квартира без номера)
        return int(digits) if digits else 9999
    
    final['sort_key'] = final['apt_key'].apply(get_sort_key)
    final = final.sort_values('sort_key').drop(columns=['sort_key'])
    
    # 7. Финальное заполнение: если в боте пусто, берем из базы
    # Если у вас есть столбец parking_time_y (из бота), он приоритетнее
    if 'parking_time_y' in final.columns:
        final['final_tariff'] = final['parking_time_y'].replace('', None).fillna(final['tariff_norm'])
    else:
        final['final_tariff'] = final['tariff_norm']
    
    # Сохранение
    output_path = os.path.join(DATA_DIR, "FLAT_REGISTRY.xlsx")
    final.to_excel(output_path, index=False)
    print(f"Файл успешно создан: {output_path}")

if __name__ == "__main__":
    run_flat_merge()
