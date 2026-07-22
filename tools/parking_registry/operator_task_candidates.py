from __future__ import annotations
import argparse, re
from datetime import datetime
from pathlib import Path
try:
    from ._common import *
    from .excel_export import write_xlsx
except ImportError:
    from _common import *
    from excel_export import write_xlsx
UA_PLATE_RE=re.compile(r"^[A-ZА-ЯІЇЄ]{2}\d{4}[A-ZА-ЯІЇЄ]{2}$",re.I)
def plate_issue(plate):
    p=str(plate or "").strip().upper()
    if not p: return "HIGH","VERIFY_PLATE","Госномер не заполнен"
    if UA_PLATE_RE.fullmatch(p): return "","",""
    digits=sum(ch.isdigit() for ch in p); letters=sum(ch.isalpha() for ch in p)
    if digits==4 and letters==0: return "HIGH","VERIFY_PLATE","В номере только 4 цифры; нужен полный госномер"
    if letters>=2 and digits==4 and len(p)<8: return "HIGH","VERIFY_PLATE","Похоже, пропущена последняя буква/буквы"
    if len(p)<8: return "HIGH","VERIFY_PLATE","Госномер короче стандартного формата"
    return "MEDIUM","VERIFY_PLATE","Госномер не похож на стандартный формат"
def has_payment(con,apt):
    if not table_exists(con,"payments") or "apartment_number" not in cols(con,"payments"): return False
    return con.execute("select 1 from payments where apartment_number=? limit 1",(apt,)).fetchone() is not None
def probe_tables(con):
    words=["task","verification","verify","queue","operator","review","correction","audit"]
    rows=[]
    for t in table_names(con):
        if any(w in t.lower() for w in words): rows.append({"table":t,"rows":con.execute(f"select count(*) from {q(t)}").fetchone()[0],"columns":", ".join(cols(con,t))})
    return rows
def main():
    ap=argparse.ArgumentParser(description="Create parking operator task candidates.")
    ap.add_argument("--db",default=None); ap.add_argument("--xlsx",action="store_true"); ap.add_argument("--output",default=None); ap.add_argument("--probe-tables",action="store_true")
    args=ap.parse_args(); db=resolve_db(args.db); con=connect(db)
    try:
        if args.probe_tables:
            rows=probe_tables(con); print("="*90); print("Possible task / verification tables"); print("="*90)
            for r in rows: print(f"{r['table']} rows={r['rows']}\n  {r['columns']}")
            if not rows: print("No candidate tables found.")
            return 0
        vehicles,src=vehicle_registry(con); tasks=[]
        for v in vehicles:
            apt=str(v.get("apartment") or "").strip(); plate=str(v.get("plate") or "").strip(); pt=str(v.get("parking_time") or "").strip()
            pr,typ,reason=plate_issue(plate)
            if typ: tasks.append({"priority":pr,"task_type":typ,"apartment":apt,"plate":plate,"vehicle_id":v.get("vehicle_id",""),"parking_time":pt,"reason":reason,"suggested_action":"Уточнить госномер у жильца или по факту на парковке","source_table":src,"origin":"AUTO"})
            if not pt and has_payment(con,apt): tasks.append({"priority":"HIGH","task_type":"FILL_PARKING_TIME","apartment":apt,"plate":plate,"vehicle_id":v.get("vehicle_id",""),"parking_time":pt,"reason":"Есть платеж по квартире, но parking_time пустой","suggested_action":"Проверить Day/Night и заполнить после подтверждения","source_table":src,"origin":"AUTO"})
    finally: con.close()
    tasks=sorted(tasks,key=apt_sort_key); counts={}
    for t in tasks: counts[t["task_type"]]=counts.get(t["task_type"],0)+1
    print("="*90); print("OSBB Parking Operator Task Candidates"); print("="*90); print("Tasks:",len(tasks))
    for k,v in sorted(counts.items()): print(f"{k}: {v}")
    for t in tasks[:30]: print(f"{t['apartment']:>4} {t['plate']:<12} {t['task_type']:<18} {t['reason']}")
    if args.xlsx:
        out=Path(args.output) if args.output else DEFAULT_OUTPUT_DIR/f"PARKING_OPERATOR_TASK_CANDIDATES_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"
        if not out.is_absolute(): out=(DEFAULT_OUTPUT_DIR.parent.parent/out).resolve() if str(out).startswith("OSBB") else (DEFAULT_OUTPUT_DIR.parent/out).resolve()
        summary=[{"metric":"task_count","value":len(tasks)}]+[{"metric":k,"value":v} for k,v in sorted(counts.items())]
        write_xlsx(out,{"Summary":summary,"Operator_Tasks":tasks}); print("Excel:",out)
    return 0
if __name__=="__main__": raise SystemExit(main())
