import pandas as pd
import os
import glob
from openpyxl import Workbook
from openpyxl.styles import Border, Side

DATA_DIR = r"G:\Programming\Py\OSBB\Data\raw\typed"

def create_payment_sheet():
    files = glob.glob(os.path.join(DATA_DIR, "OSBB_Base_Cleaned_*.xlsx"))
    if not files:
        print("Файл базы не найден.")
        return
    
    base_path = max(files, key=os.path.getmtime)
    df = pd.read_excel(base_path, dtype=str)
    
    # Готовим данные
    payment_df = pd.DataFrame({
        '№ кв': df.get('apartment_number', ''),
        'Гос. номер': df.get('license_plate', ''),
        'ФИО': df.get('owner_name', ''),
        'Сумма': '',
        'Примечание': '',
        'Подпись': ''
    })
    
    # Добавляем 10 пустых строк для "пустографки"
    empty_rows = pd.DataFrame([['', '', '', '', '', '']] * 10, columns=payment_df.columns)
    payment_df = pd.concat([payment_df, empty_rows], ignore_index=True)
    
    output_path = os.path.join(DATA_DIR, "Ведомость_оплаты_парковки.xlsx")
    
    # Стилизация
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), 
                         top=Side(style='thin'), bottom=Side(style='thin'))
    
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        payment_df.to_excel(writer, index=False, sheet_name='Ведомость')
        ws = writer.book['Ведомость']
        
        # Настройка параметров печати
        ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE # Альбомная ориентация
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        
        # Нумерация страниц (в нижнем колонтитуле по центру)
        ws.oddFooter.center.text = "Стр. &P из &N"
        
        # Настройка ширины и границ
        column_widths = {'A': 8, 'B': 15, 'C': 30, 'D': 12, 'E': 25, 'F': 15}
        for col, width in column_widths.items():
            ws.column_dimensions[col].width = width
            
        for row in ws.iter_rows(min_row=1, max_row=len(payment_df)+1, min_col=1, max_col=6):
            for cell in row:
                cell.border = thin_border

    print(f"Ведомость с пустографкой и настройками печати создана: {output_path}")

if __name__ == "__main__":
    create_payment_sheet()
