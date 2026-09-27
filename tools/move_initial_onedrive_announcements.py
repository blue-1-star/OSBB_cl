"""One-time, explicit relocation of the eight existing OSBB root files."""
import hashlib
import json
from pathlib import Path

SOURCE=Path('/Users/san/OneDrive/OSBB')
TARGET=SOURCE/'Docs/09_Announcements/Print'
NAMES=('24a_4p_20260405.xlsx','Ob_06_04_26.docx','Parking_bot.docx','QR_Sp24A.png',
       'deepseek_text_20260405_0baa9b.xlsx','Паркування.docx','Пульт_18_05_26.docx','парковка_збори_20_05_26.docx')

def main():
    manifest=SOURCE/'Docs/initial_announcements_move_20260927.json'
    if manifest.exists(): raise FileExistsError(manifest)
    rows=[]
    for name in NAMES:
        origin=SOURCE/name
        if not origin.is_file(): raise FileNotFoundError(origin)
        if (TARGET/name).exists(): raise FileExistsError(TARGET/name)
        rows.append({'old_path':str(origin),'new_path':str(TARGET/name),
                     'sha256':hashlib.sha256(origin.read_bytes()).hexdigest()})
    TARGET.mkdir(parents=True,exist_ok=True)
    manifest.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in rows:
        origin,destination=Path(row['old_path']),Path(row['new_path'])
        origin.rename(destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest()!=row['sha256']:
            raise RuntimeError(f'Checksum mismatch: {destination}')
    print(f'Moved and verified {len(rows)} files; manifest: {manifest}')

if __name__=='__main__': main()
