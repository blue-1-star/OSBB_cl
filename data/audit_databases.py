import pandas as pd
import os
import glob
from datetime import datetime

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def get_latest_base_file():
    # Находим все файлы, соответствующие шаблону
    files = glob.glob(os.path.join(DATA_DIR, "OSBB_Base_Cleaned_*.xlsx"))
    if not files:
        return None
    # Берем последний по времени изменения
    return max(files, key=os.path.getmtime)

def run_audit():
    # 1. Определение путей
    base_path = get_latest_base_file()
    bot_path = os.path.join(DATA_DIR, "parking_tbot2.xlsx")
    
    if not base_path:
        print("Ошибка: Файл эталонной базы не найден.")
        return

    # 2. Загрузка
    base = pd.read_excel(base_path, dtype=str).fillna('')
    bot = pd.read_excel(bot_path, dtype=str).fillna('')

    # Очистка ключей
    base_apts = set(base['apartment_number'].astype(str).str.strip())
    bot_apts = set(bot['Номер квартири'].astype(str).str.strip())

    # Функция сортировки
    def safe_sort_key(x):
        digits = ''.join(filter(str.isdigit, str(x)))
        return int(digits) if digits else 9999

    only_in_base = sorted(list(base_apts - bot_apts), key=safe_sort_key)
    only_in_bot = sorted(list(bot_apts - base_apts), key=safe_sort_key)

    # 3. Запись отчета с датой в имени
    date_str = datetime.now().strftime("%d_%m")
    report_name = f"DATABASE_AUDIT_REPORT_{date_str}.txt"
    
    with open(os.path.join(DATA_DIR, report_name), "w", encoding="utf-8") as f:
        f.write("=== ОТЧЕТ ПО АУДИТУ БАЗ ДАННЫХ ===\n\n")
        f.write(f"DATA_DIR = {DATA_DIR}\n\n")
        
        f.write(f"Бумажная БД: {os.path.basename(base_path)}\n")
        f.write(f"Общее число записей: {len(base)}\n")
        f.write(f"Столбцы: {list(base.columns)}\n\n")
        
        f.write(f"Ботовская БД: {os.path.basename(bot_path)}\n")
        f.write(f"Общее число записей: {len(bot)}\n")
        f.write(f"Столбцы: {list(bot.columns)}\n\n")
        
        f.write(f"--- РАСХОЖДЕНИЯ (Квартиры) ---\n")
        f.write(f"Квартиры в Бумажной базе, отсутствующие в Боте ({len(only_in_base)}):\n")
        f.write(", ".join(only_in_base) + "\n\n")
        
        f.write(f"Квартиры в Боте, отсутствующие в Бумажной базе ({len(only_in_bot)}):\n")
        f.write(", ".join(only_in_bot) + "\n\n")
        
    print(f"Отчет '{report_name}' успешно создан.")

if __name__ == "__main__":
    run_audit()
