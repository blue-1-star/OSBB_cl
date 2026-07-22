import pandas as pd
import re

def clean_phone(phone):
    if pd.isna(phone): return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 12 and digits.startswith("380"): return digits
    if len(digits) == 10 and digits.startswith("0"): return "380" + digits
    if len(digits) == 9: return "380" + digits
    return phone

def normalize_plate(plate):
    if pd.isna(plate): return ""
    cyrillic = "АВЕКМНОРСТХ"
    latin = "ABEKMHOPCTX"
    trans = str.maketrans(cyrillic, latin)
    return re.sub(r"[^A-Z0-9]", "", str(plate).upper().translate(trans))

# 1. Загрузка
file_path = r"G:\Programming\Py\OSBB\Data\raw\typed\OSBB_Final_Base.xlsx"
df = pd.read_excel(file_path, sheet_name="all")

# 2. ОЧИСТКА НОМЕРОВ КВАРТИР (Самое важное!)
# Превращаем всё в строку, убираем .0 и лишние пробелы
df['apartment_number'] = df['apartment_number'].astype(str).str.replace(r'\.0$', '', regex=True)
df['apartment_number'] = df['apartment_number'].replace({'nan': '', 'None': ''})

# 3. Очистка остальных полей
df['phone_number'] = df['phone_number'].apply(clean_phone)
df['license_plate'] = df['license_plate'].apply(normalize_plate)

# 4. Сохранение
output_path = r"G:\Programming\Py\OSBB\Data\raw\typed\OSBB_Base_Cleaned.xlsx"
df.to_excel(output_path, index=False)
print(f"Готово! Чистая база сохранена в {output_path}")
