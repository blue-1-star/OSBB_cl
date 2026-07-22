import os
import pandas as pd

# ==========================================
# 1. СЛОВАРЬ НАЗВАНИЙ СТОЛБЦОВ (MAPPING)
# ==========================================
# Ключ — оригинальное название, Значение — стандартизированное английское имя
COLUMN_MAPPING = {
    "Номер квартири": "apartment_number",
    "ПІБ власника авто": "owner_name",
    "ПІБ власника  авто": "owner_name",  # Учтен двойной пробел из файла p_2
    "Телефон": "phone_number",
    "Держ. номер": "license_plate",
    "Держномер авто": "license_plate",  # Вариант из файла p_2
    "Марка": "car_model",
    "Ночує": "parking_time",
    "День/ніч": "parking_time",  # Вариант из файла p_2
}

# Целевой порядок столбцов, который должен быть у всех листов
TARGET_COLUMNS = [
    "apartment_number",
    "owner_name",
    "phone_number",
    "license_plate",
    "car_model",
    "parking_time",
]


def process_osbb_database(main_excel_path, p2_excel_path, output_excel_path):
    print("Начало обработки данных...")

    # Сюда будем собирать очищенные DataFrames для каждого подъезда
    all_sheets_data = {}

    # ==========================================
    # ШАГ 1: ОБРАБОТКА ОСНОВНОГО ФАЙЛА (p_1, p_3, p_4, p_5, p_6)
    # ==========================================
    main_sheets = ["p_1", "p_3", "p_4", "p_5", "p_6"]

    for sheet in main_sheets:
        try:
            # Читаем конкретный лист
            # df = pd.read_excel(main_excel_path, sheet_name=sheet)
            df = pd.read_excel(main_excel_path, sheet_name=sheet, header=1)
            # Очищаем названия столбцов от случайных пробелов на концах
            df.columns = [str(col).strip() for col in df.columns]

            # Переименовываем по словарю
            df = df.rename(columns=COLUMN_MAPPING)

            # Оставляем только целевые столбцы в нужном порядке
            df = df[TARGET_COLUMNS]

            # Приводим номер квартиры к красивому виду (число или строка без .0)
            df["apartment_number"] = df["apartment_number"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)

            all_sheets_data[sheet] = df
            print(f"Лист {sheet} успешно обработан и нормализован.")
        except Exception as e:
            print(f"Ошибка при обработке листа {sheet} из основного файла: {e}")

    # ==========================================
    # ШАГ 2: ВЫТАСКИВАЕМ И АДАПТИРУЕМ ДАННЫЕ ДЛЯ p_2
    # ==========================================
    try:
        # Предполагаем, что данные на первом листе второго файла
        # df_p2 = pd.read_excel(p2_excel_path, sheet_name=0)
        df_p2 = pd.read_excel(p2_excel_path, sheet_name=0, header=1)
        # Очищаем названия столбцов
        df_p2.columns = [str(col).strip() for col in df_p2.columns]

        # Переименовываем столбцы по нашему словарю
        df_p2 = df_p2.rename(columns=COLUMN_MAPPING)

        # Вырезаем (выбираем) только нужные столбцы в строго заданном порядке
        df_p2 = df_p2[TARGET_COLUMNS]

        # Очистка номеров квартир
        df_p2["apartment_number"] = df_p2["apartment_number"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)

        all_sheets_data["p_2"] = df_p2
        print("Данные для p_2 успешно извлечены, отфильтрованы и нормализованы.")
    except Exception as e:
        print(f"Ошибка при обработке файла для p_2: {e}")

    # ==========================================
    # ШАГ 3: ФИНАЛ — СЛИЯНИЕ ВСЕХ ДАННЫХ В ЛИСТ "all"
    # ==========================================
    combined_list = []

    # Проходим по порядку от 1 до 6, чтобы собрать общую базу упорядоченно
    for i in range(1, 7):
        sheet_key = f"p_{i}"
        if sheet_key in all_sheets_data:
            df_temp = all_sheets_data[sheet_key].copy()

            # Полезное улучшение: добавляем колонку подъезда в общую базу,
            # чтобы в листе 'all' было понятно, откуда машина
            df_temp.insert(0, "entrance", i)

            combined_list.append(df_temp)

    # Объединяем все DataFrame в один
    df_all = pd.concat(combined_list, ignore_index=True)
    print(f"Создана единая база 'all'. Всего записей: {len(df_all)}")

    # ==========================================
    # ШАГ 4: ЗАПИСЬ В ВЫХОДНОЙ ФАЙЛ EXCEL
    # ==========================================
    with pd.ExcelWriter(output_excel_path, engine="openpyxl") as writer:
        # 1. Первым пишем лист 'all'
        df_all.to_excel(writer, sheet_name="all", index=False)

        # 2. Затем пишем по порядку листы p_1, p_2 ... p_6
        for i in range(1, 7):
            sheet_key = f"p_{i}"
            if sheet_key in all_sheets_data:
                all_sheets_data[sheet_key].to_excel(
                    writer, sheet_name=sheet_key, index=False
                )

    print(f"\nПроцесс завершен! Файл сохранен по пути: {output_excel_path}")


# --- Настройки путей к файлам ---
if __name__ == "__main__":
    # Папка с данными
    DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

    # 1. Файл, где лежат листы p_1, p_3, p_4, p_5, p_6
    main_file = os.path.join(DATA_DIR, "p_all_tables.xlsx")

    # 2. Файл, откуда нужно вырезать данные для подъезда 2
    # НАПРАВЬТЕ НА ВАШ ФАЙЛ (измените имя 'source_p2.xlsx' на реальное)
    p2_file = os.path.join(DATA_DIR, "Список Авто_1.xlsx")

    # 3. Итоговый файл с листом 'all' на первом месте
    result_file = os.path.join(DATA_DIR, "OSBB_Final_Base.xlsx")

    process_osbb_database(main_file, p2_file, result_file)
