import pandas as pd
import os

def check_passport_columns(passport_path):
    print(f"Пытаюсь открыть файл: {passport_path}")
    
    if not os.path.exists(passport_path):
        print("Ошибка: Файл не найден по указанному пути!")
        return

    try:
        # Читаем только первую строку, чтобы увидеть заголовки
        df = pd.read_excel(passport_path, nrows=1)
        
        print("\n--- ОБНАРУЖЕННЫЕ СТОЛБЦЫ ---")
        # Выводим столбцы в виде списка, чтобы видеть каждый как отдельный элемент
        cols = list(df.columns)
        for i, col in enumerate(cols):
            #repr() покажет скрытые символы типа переноса строки или лишних пробелов
            print(f"Столбец {i}: {repr(col)}")
            
        print("\n--- ПРОВЕРКА ---")
        required = ['№кв-ри', 'заг.пл-ща', 'Телефон', 'П.І.Б']
        for r in required:
            if r in cols:
                print(f"[OK] '{r}' найден.")
            else:
                print(f"[!] '{r}' НЕ найден.")
                
    except Exception as e:
        print(f"Ошибка при чтении файла: {e}")

if __name__ == "__main__":
    # УКАЖИТЕ ПУТЬ К ВАШЕМУ ФАЙЛУ
    PATH = r"G:\Programming\Py\OSBB\Data\raw\typed\house_passport.xlsx"
    check_passport_columns(PATH)
