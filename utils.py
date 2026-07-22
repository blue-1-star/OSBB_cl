import re
import pandas as pd


def norm_text(value):
    if pd.isna(value):
        return None
    value = str(value).strip()
    return value if value else None


def norm_apartment(value):
    if pd.isna(value):
        return None

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    text = str(value).strip()

    if text.endswith(".0"):
        text = text[:-2]

    return text if text else None




# --------------------------------------------------
# Нормализация букв номерных знаков
# --------------------------------------------------
# Цель: исправить ввод украинскими/русскими буквами,
# визуально похожими на латинские.
#
# Примеры:
# АА9888ЄС -> AA9888EC
# АА8393УВ -> AA8393YB
# КА8009ІВ -> KA8009IB
# --------------------------------------------------

CYR_TO_LAT_PLATE = str.maketrans({
    # Базовые визуальные совпадения
    "А": "A", "а": "A",
    "В": "B", "в": "B",
    "Е": "E", "е": "E",
    "К": "K", "к": "K",
    "М": "M", "м": "M",
    "Н": "H", "н": "H",
    "О": "O", "о": "O",
    "Р": "P", "р": "P",
    "С": "C", "с": "C",
    "Т": "T", "т": "T",
    "Х": "X", "х": "X",

    # Украинские буквы, часто встречающиеся при вводе номера
    "І": "I", "і": "I",
    "Ї": "I", "ї": "I",
    "Є": "E", "є": "E",
    "Ґ": "G", "ґ": "G",

    # Не визуальные, но практические ошибки раскладки
    "У": "Y", "у": "Y",
})

COLOR_MAP = {
    "белый": "WHITE", "білий": "WHITE", "white": "WHITE",
    "черный": "BLACK", "чорний": "BLACK", "black": "BLACK",
    "серый": "GRAY", "сірий": "GRAY", "gray": "GRAY", "grey": "GRAY",
    "серебристый": "SILVER", "сріблястий": "SILVER", "silver": "SILVER",
    "синий": "BLUE", "синій": "BLUE", "blue": "BLUE",
    "красный": "RED", "червоний": "RED", "red": "RED",
    "зеленый": "GREEN", "зелений": "GREEN", "green": "GREEN",
    "коричневый": "BROWN", "коричневий": "BROWN", "brown": "BROWN",
    "желтый": "YELLOW", "жовтий": "YELLOW", "yellow": "YELLOW",
    "бежевий": "BEIGE",
    "бордо": "BURGUNDY",
    "золотистий": "GOLD",
    "мідний": "COPPER",
    "перламутр": "PEARL",
    "срібло": "SILVER",
    "сіра": "GRAY",
    "темно сірий": "DARK_GRAY",
    "темно-сірий": "DARK_GRAY",
    "темная вишня": "DARK_CHERRY",
    "чарна": "BLACK",
}


MODEL_MAP = {
    "nissan": "NISSAN", "ниссан": "NISSAN", "ніссан": "NISSAN",
    "toyota": "TOYOTA", "тойота": "TOYOTA",
    "volkswagen": "VOLKSWAGEN", "vw": "VOLKSWAGEN", "фольксваген": "VOLKSWAGEN",
    "renault": "RENAULT", "рено": "RENAULT",
    "hyundai": "HYUNDAI", "хендай": "HYUNDAI", "хюндай": "HYUNDAI",
    "kia": "KIA", "кіа": "KIA",
    "skoda": "SKODA", "шкода": "SKODA",
    "mazda": "MAZDA", "мазда": "MAZDA",
    "ford": "FORD", "форд": "FORD",
    "mercedes": "MERCEDES", "мерседес": "MERCEDES",
    "bmw": "BMW", "бмв": "BMW",
    "audi": "AUDI", "ауди": "AUDI", "ауді": "AUDI",
    "нисан": "NISSAN",
    "нісан": "NISSAN",

    "хонда": "HONDA",
    "honda": "HONDA",

    "лексус": "LEXUS",
    "lexus": "LEXUS",

    "ягуар": "JAGUAR",
    "jaguar": "JAGUAR",

    "субару": "SUBARU",
    "subaru": "SUBARU",

    "порш": "PORSCHE",
    "porsche": "PORSCHE",

    "сітроен": "CITROEN",
    "ситроен": "CITROEN",
    "citroen": "CITROEN",

    "шевроле": "CHEVROLET",
    "chevrolet": "CHEVROLET",

    "фольцваген": "VOLKSWAGEN",

    "міцубісі": "MITSUBISHI",
    "митсубиси": "MITSUBISHI",

    "ленд ровер": "LAND ROVER",
    "land rover": "LAND ROVER",

    "рейндж ровер": "RANGE ROVER",
    "range rover": "RANGE ROVER",

    "мерседес-бенц": "MERCEDES",
    "mercedes-benz": "MERCEDES",
    "opel": "OPEL",
    "опель": "OPEL",
}

def normalize_mixed_alphabet(text):
    if not text:
        return text

    return str(text).translate(CYR_TO_LAT_PLATE)

def normalize_plate(value):
    text = norm_text(value)

    if not text:
        return None, "MISSING"

    text = text.upper()
    text = text.translate(CYR_TO_LAT_PLATE)
    text = re.sub(r"[^A-Z0-9]", "", text)

    if not text:
        return None, "MISSING"

    if re.fullmatch(r"[A-Z]{2}\d{4}[A-Z]{2}", text):
        return text, "STANDARD"

    return text, "SUSPICIOUS"


def extract_phones(value):
    text = norm_text(value)

    if not text:
        return []

    candidates = re.findall(r"\+?\d[\d\s\-\(\)]{7,}\d", text)
    result = []

    for item in candidates:
        digits = re.sub(r"\D", "", item)

        if digits.startswith("380") and len(digits) == 12:
            result.append("+" + digits)
        elif digits.startswith("80") and len(digits) == 11:
            result.append("+3" + digits)
        elif digits.startswith("0") and len(digits) == 10:
            result.append("+38" + digits)
        elif len(digits) == 9:
            result.append("+380" + digits)

    return sorted(set(result))


def normalize_phone(value):
    phones = extract_phones(value)
    return "; ".join(phones) if phones else None


def normalize_color(value):
    text = norm_text(value)

    if not text:
        return None
    if text in ("-", "--"):
        return None
    key = text.lower().strip()
    key = re.sub(r"\s+", " ", key)

    return COLOR_MAP.get(key)


def normalize_car_model(value):
    text = norm_text(value)

    if not text:
        return None

    key = text.lower().strip()
    key = re.sub(r"\s+", " ", key)
    first_word = key.split()[0]

    # 1. Сначала обычная проверка как написано
    if key in MODEL_MAP:
        return MODEL_MAP[key]

    if first_word in MODEL_MAP:
        return MODEL_MAP[first_word]

    # 2. Потом пробуем исправить смешанный алфавит
    mixed = normalize_mixed_alphabet(text)

    mixed_key = mixed.lower().strip()
    mixed_key = re.sub(r"\s+", " ", mixed_key)
    mixed_first_word = mixed_key.split()[0]

    if mixed_key in MODEL_MAP:
        return MODEL_MAP[mixed_key]

    if mixed_first_word in MODEL_MAP:
        return MODEL_MAP[mixed_first_word]

    # 3. Если уже чистая латиница — вернуть марку uppercase
    if re.fullmatch(r"[a-zA-Z0-9\-]+", mixed_first_word):
        return mixed_first_word.upper()

    return None

def apartment_is_numeric(apartment_number):
    if apartment_number is None:
        return False

    return str(apartment_number).strip().isdigit()


def apartment_sort_sql(field_name="apartment_number"):
    """
    SQL-сортировка квартир:
    сначала числовые квартиры по возрастанию,
    потом текстовые объекты.
    """

    return f"""
        CASE
            WHEN {field_name} GLOB '[0-9]*'
            THEN CAST({field_name} AS INTEGER)
            ELSE 999999
        END,
        {field_name}
    """