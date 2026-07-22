import pandas as pd
import os

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def create_car_registry():
    # 1. Загрузка
    base = pd.read_excel(os.path.join(DATA_DIR, "OSBB_Base_Cleaned.xlsx"), dtype=str)
    bot = pd.read_excel(os.path.join(DATA_DIR, "parking_tbot1.xlsx"), dtype=str)
    
    # 2. Подготовка "бумажной" базы (делаем 1 строка = 1 авто)
    # Если в бумажной базе несколько авто в одной ячейке (через запятую), 
    # этот код их разделит (если они там записаны через запятую)
    base = base.assign(license_plate=base['license_plate'].str.split(', ')).explode('license_plate')
    base['license_plate'] = base['license_plate'].str.strip()
    base['WhatBase'] = 'Paper'
    
    # 3. Подготовка "ботовской" базы
    bot = bot.rename(columns={
        'Номер Авто': 'plate_bot', 
        'Номер квартири': 'apt_bot',
        'ПІБ': 'name_bot'
    })
    bot['WhatBase'] = 'Bot'
    
    # 4. Соединение по номеру авто
    # Используем outer join по номеру авто, чтобы увидеть все машины из обоих списков
    registry = pd.merge(
        base, 
        bot, 
        left_on='license_plate', 
        right_on='plate_bot', 
        how='outer'
    )
    
    # 5. Объединение колонок (если машина есть в обеих базах)
    registry['license_plate'] = registry['license_plate'].fillna(registry['plate_bot'])
    
    # 6. Финальный отбор столбцов
    cols = ['license_plate', 'car_model', 'WhatBase', 
            'apartment_number', 'owner_name', 'phone_base', 
            'name_bot', 'plate_bot', 'apt_bot']
    
    # Оставляем только те, что реально есть в итоговой таблице
    final_cols = [c for c in cols if c in registry.columns]
    registry = registry[final_cols]
    
    # 7. Сохранение
    registry.to_excel(os.path.join(DATA_DIR, "CAR_REGISTRY_ALL.xlsx"), index=False)
    print("Готово! Создан файл 'CAR_REGISTRY_ALL.xlsx'. Каждая строка — это один автомобиль.")

if __name__ == "__main__":
    create_car_registry()
