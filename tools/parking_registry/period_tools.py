from __future__ import annotations
import re

def compact_period(period:str|None)->str:
    text=str(period or "").strip()
    m=re.fullmatch(r"(\d{4})-(\d{2})[_\-](\d{4})-(\d{2})", text)
    if m and m.group(1)==m.group(3): return f"{m.group(2)}-{m.group(4)}_{m.group(1)[2:]}"
    m=re.fullmatch(r"(\d{1,2})-(\d{1,2})\.(\d{4})", text)
    if m: return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}_{m.group(3)[2:]}"
    return text
