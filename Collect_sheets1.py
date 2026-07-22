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
    all_sheets_data = {}

    # ==========================================
    # ШАГ 1: АВТОМАТИЧЕСКОЕ ЧТЕНИЕ ВСЕХ СУЩЕСТВУЮЩИХ ЛИСТОВ
    # ==========================================
    try:
        # Узнаем, какие листы вообще есть в файле p_all_tables.xlsx
        xl = pd.ExcelFile(main_excel_path)
        available_sheets = xl.sheet_names
        print(f"Доступные листы в основном файле: {available_sheets}")
    except Exception as e:
        print(f"Критическая ошибка при открытии основного файла: {e}")
        return

    for sheet in available_sheets:
        # Пропускаем p_2, если он вдруг там затесался пустой, мы его возьмем из другого файла
        if sheet == "p_2":
            continue

        try:
            # Читаем со второй строки (header=1)
            df = pd.read_excel(main_excel_path, sheet_name=sheet, header=1)
            df.columns = [str(col).strip() for col in df.columns]
            df = df.rename(columns=COLUMN_MAPPING)

            # Проверяем столбцы
            missing_cols = [
                col for col in TARGET_COLUMNS if col not in df.columns
            ]
            if missing_cols:
                print(
                    f"[Пропуск] На листе {sheet} не найдены столбцы после переименования."
                )
                print(f"Фактические столбцы: {list(df.columns)}")
                continue

            df = df[TARGET_COLUMNS]
            df["apartment_number"] = (
                df["apartment_number"]
                .fillna("")
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )

            all_sheets_data[sheet] = df
            print(f"Лист {sheet} успешно обработан.")
        except Exception as e:
            print(f"Ошибка при обработке листа {sheet}: {e}")

    # ==========================================
    # ШАГ 2: ЧТЕНИЕ ВТОРОГО ПОДЪЕЗДА (header=2, так как 3-я строка)
    # ==========================================
    print("\nПробуем загрузить данные для p_2 из отдельного файла...")
    try:
        # header=2 означает, что 3-я строка файла — это заголовки
        df_p2 = pd.read_excel(p2_excel_path, sheet_name=0, header=2)
        df_p2.columns = [str(col).strip() for col in df_p2.columns]
        df_p2 = df_p2.rename(columns=COLUMN_MAPPING)

        missing_cols_p2 = [
            col for col in TARGET_COLUMNS if col not in df_p2.columns
        ]

        if missing_cols_p2:
            print(
                f"[Ошибка p_2] Столбцы не совпали! Проверьте 3-ю строку в файле."
            )
            print(f"Вот что Python увидел в 3-й строке: {list(df_p2.columns)}")
            print(
                "Если имена столбцов верные, возможно, заголовки в строке 2 или 4? Попробуйте поменять header=1 или header=3"
            )
        else:
            df_p2 = df_p2[TARGET_COLUMNS]
            df_p2["apartment_number"] = (
                df_p2["apartment_number"]
                .fillna("")
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )

            all_sheets_data["p_2"] = df_p2
            print("Данные для p_2 успешно импортированы!")

    except Exception as e:
        print(f"Не удалось обработать файл p_2. Ошибка: {e}")

    # ==========================================
    # ШАГ 3: СЛИЯНИЕ В ОДИН ЛИСТ "all"
    # ==========================================
    print("\nОбъединяем данные...")
    combined_list = []

    # Строго собираем от 1 до 6 по порядку
    for i in range(1, 7):
        sheet_key = f"p_{i}"
        if sheet_key in all_sheets_data:
            df_temp = all_sheets_data[sheet_key].copy()
            df_temp.insert(
                0, "entrance", i
            )  # Добавляем номер подъезда первым столбцом
            combined_list.append(df_temp)

    if not combined_list:
        print("Критическая ошибка: нечего объединять, все листы пустые.")
        return

    df_all = pd.concat(combined_list, ignore_index=True)

    # ==========================================
    # ШАГ 4: ЗАПИСЬ
    # ==========================================
    with pd.ExcelWriter(output_excel_path, engine="openpyxl") as writer:
        # Сначала общий лист
        df_all.to_excel(writer, sheet_name="all", index=False)
        # Затем каждый подъезд на свою вкладку
        for i in range(1, 7):
            sheet_key = f"p_{i}"
            if sheet_key in all_sheets_data:
                all_sheets_data[sheet_key].to_excel(
                    writer, sheet_name=sheet_key, index=False
                )

    print(f"\nГотово! Результат сохранен в: {output_excel_path}")
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
