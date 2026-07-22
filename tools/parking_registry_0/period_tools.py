from __future__ import annotations
import re

def split_period_atoms(label:str|None)->list[str]:
    text=str(label or "").strip()
    if not text: return []
    m=re.fullmatch(r"(\d{1,2})-(\d{1,2})\.(\d{4})", text)
    if m:
        a,b,y=m.groups(); a=int(a); b=int(b)
        if 1<=a<=b<=12: return [f"{y}-{m:02d}" for m in range(a,b+1)]
    m=re.fullmatch(r"(\d{1,2})\.(\d{4})", text)
    if m:
        mo,y=m.groups(); return [f"{y}-{int(mo):02d}"]
    return [text]
