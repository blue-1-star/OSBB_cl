import pandas as pd
import re
import os

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def normalize_apt_strict(apt):
    return re.sub(r'\D', '', str(apt).split('.')[0]).lstrip('0')

def normalize_plate(plate):
    """Исправленный словарь: добавлена украинская 'І'."""
    # Добавили 'І' в кириллицу и 'I' в латиницу
    cyrillic = "АВЕКМНОРСТХІ" 
    latin = "ABEKMHOPCTXI"
    trans = str.maketrans(cyrillic, latin)
    return re.sub(r"[^A-Z0-9]", "", str(plate).upper().translate(trans))

def final_strict_merge():
    base_df = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx"), dtype=str)
    bot_df = pd.read_excel(os.path.join(DATA_DIR, "parking_tbot1.xlsx"), dtype=str)
    
    base_df['apt_norm'] = base_df['apartment_number'].apply(normalize_apt_strict)
    base_df['plt_norm'] = base_df['license_plate'].apply(normalize_plate)
    bot_df['apt_norm'] = bot_df['Номер квартири'].apply(normalize_apt_strict)
    bot_df['plt_norm'] = bot_df['Номер Авто'].apply(normalize_plate)
    
    # --- ОБНОВЛЕНИЕ ---
    for idx, row in base_df.iterrows():
        match = bot_df[(bot_df['apt_norm'] == row['apt_norm']) & (bot_df['plt_norm'] == row['plt_norm'])]
        
        # Правим тариф (всегда, для всех записей)
        p_time = str(row.get('parking_time', '')).strip()
        if p_time in ['+', '500', 'Night']:
            base_df.at[idx, 'parking_time'] = 'Night'
        else:
            base_df.at[idx, 'parking_time'] = 'Day'
            
        if not match.empty:
            m = match.iloc[0]
            if pd.isna(row['phone_number']) or row['phone_number'] == 'nan':
                base_df.at[idx, 'phone_number'] = m.get('Телефон', '')
            base_df.at[idx, 'car_color'] = m.get('Колір авто', '')
            base_df.at[idx, 'is_owner'] = 'True' if str(m.get('Власність', '')).lower() == 'власник' else 'False'
    
    # --- ДОБАВЛЕНИЕ НОВЫХ ---
    new_records = []
    for _, b_row in bot_df.iterrows():
        if not ((base_df['apt_norm'] == b_row['apt_norm']) & (base_df['plt_norm'] == b_row['plt_norm'])).any():
            new_records.append({
                'apartment_number': b_row['Номер квартири'],
                'owner_name': b_row.get('ПІБ', ''),
                'phone_number': b_row.get('Телефон', ''),
                'license_plate': b_row.get('Номер Авто', ''),
                'car_color': b_row.get('Колір авто', ''),
                'is_owner': 'True' if str(b_row.get('Власність', '')).lower() == 'власник' else 'False',
                'parking_time': 'Day' # Новые по умолчанию Day
            })
            
    final_df = pd.concat([base_df, pd.DataFrame(new_records)], ignore_index=True)
    
    # Финальная очистка
    final_df.drop(columns=['apt_norm', 'plt_norm'], inplace=True)
    final_df.to_excel(os.path.join(DATA_DIR, "OSBB_MASTER_DATABASE.xlsx"), index=False)
    print("Мастер-база готова с исправленной нормализацией номеров и тарифами!")

if __name__ == "__main__":
    final_strict_merge()
