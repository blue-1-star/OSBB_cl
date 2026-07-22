import os
import re
import pandas as pd

# ==========================================
# 1. СПРАВОЧНИКИ ДЛЯ СТАНДАРТИЗАЦИИ
# ==========================================

# Замена кириллических букв на латинские (похожие по написанию) для госномеров
CYRILLIC_TO_LATIN = {
    "А": "A",
    "В": "B",
    "Е": "E",
    "К": "K",
    "М": "M",
    "Н": "H",
    "О": "O",
    "Р": "P",
    "С": "C",
    "Т": "T",
    "Х": "X",
    "І": "I",
}

# Словарь для исправления марок авто
CAR_BRAND_MAPPING = {
    "рено": "Renault",
    "бмв": "BMW",
    "тойота": "Toyota",
    "мерседес": "Mercedes-Benz",
    "ауди": "Audi",
    "фольксваген": "Volkswagen",
    "гольф": "Volkswagen",
    "пасat": "Volkswagen",
    "хюндай": "Hyundai",
    "хундай": "Hyundai",
    "киа": "KIA",
    "мазда": "Mazda",
    "ниссан": "Nissan",
    "форд": "Ford",
    "шкода": "Skoda",
    "ваз": "VAZ",
    "лада": "Lada",
}


# ==========================================
# 2. ФУНКЦИИ ОЧИСТКИ (CLEANING FUNCTIONS)
# ==========================================


def clean_license_plate(plate):
    """Приводит госномер к единому латинскому стандарту без пробелов."""
    if pd.isna(plate):
        return ""
    plate = str(plate).strip().upper()
    plate = re.sub(r"\s+", "", plate)  # Удаляем все пробелы

    # Заменяем кириллицу на латиницу
    cleaned = ""
    for char in plate:
        cleaned += CYRILLIC_TO_LATIN.get(char, char)

    return cleaned


def clean_phone(phone):
    """Очищает телефон от мусора и приводит к формату 380XXXXXXXXX."""
    if pd.isna(phone):
        return ""

    # Обработка научного формата Excel (Scientific notation вроде 3.8093e+11)
    phone_str = str(phone).strip()
    if "e" in phone_str.lower() or "E" in phone_str.lower():
        try:
            phone_str = f"{float(phone_str):.0f}"
        except:
            pass

    # Оставляем только цифры
    digits = re.sub(r"\D", "", phone_str)

    if not digits:
        return ""

    # Приводим к стандарту 380...
    if digits.startswith("0") and len(digits) == 10:
        return "380" + digits
    elif digits.startswith("80") and len(digits) == 11:
        return "3" + digits
    elif digits.startswith("380") and len(digits) == 12:
        return digits
    elif len(digits) == 9:
        return "380" + digits

    return digits  # Возвращаем как есть, если нестандартная длина (пойдет в лог ошибок)


def clean_parking_time(val):
    """Превращает хаос парковки в True (ночует) / False (нет) / 'CHECK' (непонятно)."""
    if pd.isna(val):
        return "CHECK"

    val_str = str(val).strip().lower()

    if val_str in [
        "+",
        "500",
        "так",
        "да",
        "ночует",
        "ночує",
        "true",
        "1",
        "1.0",
    ]:
        return True
    if val_str in [
        "-",
        "гараж",
        "ні",
        "нет",
        "false",
        "0",
        "0.0",
        "не ночує",
        "стоянка",
    ]:
        return False

    return "CHECK"  # Требует ручной проверки


def clean_car_model(model):
    """Исправляет очевидные опечатки в марках авто по словарю."""
    if pd.isna(model):
        return ""
    model_str = str(model).strip()
    model_lower = model_str.lower()

    for ru_brand, en_brand in CAR_BRAND_MAPPING.items():
        if ru_brand in model_lower:
            return en_brand  # Возвращаем каноничное английское название

    return model_str  # Если совпадений нет, оставляем оригинал


# ==========================================
# 3. ОСНОВНОЙ ПРОЦЕСС ОБРАБОТКИ
# ==========================================


def run_verification(our_base_path, bot_base_path, report_output_path):
    print("Запуск верификации данных...")

    # Загружаем нашу созданную базу (лист all)
    df_our = pd.read_excel(our_base_path, sheet_name="all")
    # Загружаем базу бота (предполагаем, первый лист)
    df_bot = pd.read_excel(bot_base_path, sheet_name=0)

    # Очищаем заголовки ботовой базы
    df_bot.columns = [str(col).strip() for col in df_bot.columns]

    # --- СТЕП 1: Нормализация нашей базы ---
    print("Нормализация нашей базы...")
    df_our["clean_plate"] = df_our["license_plate"].apply(clean_license_plate)
    df_our["clean_phone"] = df_our["phone_number"].apply(clean_phone)
    df_our["clean_parking"] = df_our["parking_time"].apply(clean_parking_time)
    df_our["clean_model"] = df_our["car_model"].apply(clean_car_model)

    # --- СТЕП 2: Нормализация базы бота ---
    print("Нормализация базы бота...")
    # Названия в боте: Власність | ПІБ | Телефон | Номер квартири | Марка авто | Колір авто | Номер Авто | Статус
    df_bot["clean_plate"] = df_bot["Номер Авто"].apply(clean_license_plate)
    df_bot["clean_phone"] = df_bot["Телефон"].apply(clean_phone)
    df_bot["clean_model"] = df_bot["Марка авто"].apply(clean_car_model)

    # ==========================================
    # ЧАСТЬ 3: ГЕНЕРАЦИЯ ОТЧЕТОВ-ЗАДАНИЙ ДЛЯ ТСЖ
    # ==========================================

    # --- ЗАДАНИЕ 1: Ошибки в госномерах (неполные или пустые номера у нас) ---
    # Украинский номер должен быть обычно от 7 до 8 символов (старые форматы бывают 5-6)
    invalid_plates_our = df_our[
        (df_our["clean_plate"].str.len() < 5) | (df_our["clean_plate"] == "")
    ]

    # Ищем, нет ли подсказки для этих квартир в базе бота по номеру телефона
    plates_to_fix = []
    for idx, row in invalid_plates_our.iterrows():
        bot_match = df_bot[
            (df_bot["clean_phone"] == row["clean_phone"])
            & (df_bot["clean_phone"] != "")
        ]
        bot_plate = bot_match["Номер Авто"].values[0] if not bot_match.empty else "Не найдено в боте"
        plates_to_fix.append(
            {
                "Entrance (Подъезд)": row["entrance"],
                "Apartment (Квартира)": row["apartment_number"],
                "Owner (ФИО у нас)": row["owner_name"],
                "Our Plate (Номер у нас)": row["license_plate"],
                "Suggested Bot Plate (Номер из Бота)": bot_plate,
                "Phone": row["phone_number"],
            }
        )
    df_report_plates = pd.DataFrame(plates_to_fix)

    # --- ЗАДАНИЕ 2: Данные, которых ВООБЩЕ НЕТ у нас, но они есть в Боте ---
    # Ищем номера машин из бота, которых нет в нашей clean_plate
    new_cars_from_bot = df_bot[~df_bot["clean_plate"].isin(df_our["clean_plate"])]

    # Исключаем пустые номера бота из этого списка
    new_cars_from_bot = new_cars_from_bot[new_cars_from_bot["clean_plate"] != ""]

    report_new_cars = []
    for idx, row in new_cars_from_bot.iterrows():
        is_owner = (
            True if str(row["Власність"]).strip().lower() == "власник" else False
        )
        report_new_cars.append(
            {
                "Bot_Plate (Номер авто)": row["Номер Авто"],
                "Bot_Apartment (Квартира)": row["Номер квартири"],
                "Bot_Name (ФИО)": row["ПІБ"],
                "Bot_Phone (Телефон)": row["Телефон"],
                "Bot_Car (Марка)": row["Марка авто"],
                "Bot_Color (Цвет)": row["Колір авто"],
                "Is_Owner (Владелец квартиры?)": is_owner,
                "Bot_Status (Статус)": row["Статус"],
            }
        )
    df_report_new = pd.DataFrame(report_new_cars)

    # --- ЗАДАНИЕ 3: Разбор полетов по Парковке (Непонятные статусы) ---
    df_report_parking = df_our[df_our["clean_parking"] == "CHECK"][
        ["entrance", "apartment_number", "owner_name", "parking_time"]
    ].rename(columns={"parking_time": "Исходное непонятное значение"})

    # --- ЗАДАНИЕ 4: Лог изменений по маркам автомобилей (До/После) ---
    changed_models = df_our[df_our["car_model"] != df_our["clean_model"]][
        ["owner_name", "car_model", "clean_model"]
    ].drop_duplicates()
    df_report_models = changed_models.rename(
        columns={"car_model": "Было (В файле)", "clean_model": "Стало (English)"}
    )

    # ==========================================
    # 4. СОХРАНЕНИЕ ОТЧЕТА В EXCEL
    # ==========================================
    with pd.ExcelWriter(report_output_path, engine="openpyxl") as writer:
        df_report_new.to_excel(
            writer, sheet_name="1. Накатить из Бота (Новые)", index=False
        )
        df_report_plates.to_excel(
            writer, sheet_name="2. Исправить номера авто", index=False
        )
        df_report_parking.to_excel(
            writer, sheet_name="3. Уточнить парковку", index=False
        )
        df_report_models.to_excel(
            writer, sheet_name="4. Лог исправлений марок", index=False
        )

    print(f"\n[Успех] Отчет верификации создан: {report_output_path}")
    print("Откройте этот файл, проверьте глазами листы-задания перед вливанием!")


# --- Настройка путей ---
if __name__ == "__main__":
    DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

    our_base = os.path.join(DATA_DIR, "OSBB_Final_Base.xlsx")  # Наша база с листом 'all'
    bot_base = os.path.join(DATA_DIR, "parking_tbot1.xlsx")  # Экспорт из Телеграм-бота (измените имя если надо)
    report_file = os.path.join(DATA_DIR, "OSBB_Verification_Report.xlsx")

    run_verification(our_base, bot_base, report_file)