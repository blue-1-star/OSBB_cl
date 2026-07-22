import pandas as pd
import os
from datetime import datetime

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def classify_tariff(val):
    # Приводим к строке, убираем пробелы
    val = str(val).strip()
    # Night: +, 500
    if val in ['+', '500', '500.0']: return 'Night'
    # Day: -, Гараж
    if val in ['-', 'Гараж']: return 'Day'
    return ''

def standardize_and_normalize():
    # Загружаем базу как строки
    file_path = os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx")
    df = pd.read_excel(file_path, dtype=str)
    
    # 1. Нормализация квартир (текст, без ".0")
    df['apartment_number'] = df['apartment_number'].astype(str).str.strip().replace('\.0$', '', regex=True)
    
    # 2. Нормализация тарифов (создаем/обновляем столбец)
    if 'parking_time' in df.columns:
        df['norm_tariff'] = df['parking_time'].apply(classify_tariff)
    else:
        df['norm_tariff'] = ''
        print("Внимание: столбец 'parking_time' не найден, создан пустой 'norm_tariff'.")
    
    # 3. Формируем имя файла с текущей датой
    date_str = datetime.now().strftime("%d_%m")
    output_name = f"OSBB_Base_Cleaned_{date_str}.xlsx"
    output_path = os.path.join(DATA_DIR, output_name)
    
    # 4. Сохранение
    df.to_excel(output_path, index=False)
    
    print(f"Готово! Файл сохранен как: {output_name}")
    print("Данные очищены, тарифы нормализованы.")

if __name__ == "__main__":
    standardize_and_normalize()
