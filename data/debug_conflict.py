import pandas as pd

def debug_conflict(val_base, val_bot):
    print(f"Сравнение: '{val_base}' и '{val_bot}'")
    print(f"Длина: {len(str(val_base))} vs {len(str(val_bot))}")
    print(f"Коды символов: {[ord(c) for c in str(val_base)]} vs {[ord(c) for c in str(val_bot)]}")

import pandas as pd
import os

# Путь к папке с данными
DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def create_clean_alignment():
    # 1. Загрузка
    try:
        base = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx"), dtype=str)
        bot = pd.read_excel(os.path.join(DATA_DIR, "parking_tbot1.xlsx"), dtype=str)
    except FileNotFoundError as e:
        print(f"Ошибка: Не найден файл. Проверьте пути: {e}")
        return

    # 2. Подготовка базы (группируем машины по квартире)
    # Используем str(i) для безопасности и фильтруем пустые значения
    base_grouped = base.groupby('apartment_number').agg({
        'owner_name': 'first',
        'license_plate': lambda x: ', '.join(sorted([str(i) for i in x.unique() if pd.notnull(i) and str(i).lower() != 'nan'])),
        'car_model': lambda x: ', '.join(sorted([str(i) for i in x.unique() if pd.notnull(i) and str(i).lower() != 'nan']))
    }).reset_index()
    
    # 3. Подготовка бота
    bot = bot.rename(columns={'Номер квартири': 'apt_bot', 'ПІБ': 'name_bot', 'Номер Авто': 'plate_bot'})
    
    # 4. Слияние (Outer Join)
    result = pd.merge(base_grouped, bot, left_on='apartment_number', right_on='apt_bot', how='outer')
    
    # 5. Функция для корректной числовой сортировки (например, 1 < 2 < 10)
    def to_int(val):
        try:
            # Извлекаем все цифры из строки
            s = str(val)
            digits = ''.join(filter(str.isdigit, s))
            return int(digits) if digits else 99999
        except:
            return 99999
            
    # Применяем сортировку к объединенному ключу квартиры
    result['sort_key'] = result['apartment_number'].fillna(result['apt_bot']).apply(to_int)
    result = result.sort_values(by='sort_key')
    
    # 6. Финальная очистка и порядок колонок
    cols = ['apartment_number', 'owner_name', 'license_plate', 'car_model', 
            'apt_bot', 'name_bot', 'plate_bot']
    # Оставляем только те, что реально есть в таблице
    final_cols = [c for c in cols if c in result.columns]
    result = result[final_cols]
    
    # Сохранение
    output_path = os.path.join(DATA_DIR, "CLEAN_COMPARISON_GROUPED.xlsx")
    result.to_excel(output_path, index=False)
    print(f"Готово! Файл сохранен: {output_path}")

if __name__ == "__main__":
    create_clean_alignment()
