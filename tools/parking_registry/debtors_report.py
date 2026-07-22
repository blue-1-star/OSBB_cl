from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path
try:
    from ._common import *
    from .excel_export import write_xlsx
except ImportError:
    from _common import *
    from excel_export import write_xlsx
def debt_sources(con):
    words=["debt","balance","charge","accrual","arrear","борг","долг"]
    return [{"table":t,"columns":", ".join(cols(con,t))} for t in table_names(con) if any(w in t.lower() for w in words)]
def build(con):
    vehicles,src=vehicle_registry(con); by_apt={}
    for v in vehicles:
        apt=str(v.get("apartment") or "").strip()
        if apt: by_apt.setdefault(apt,[]).append(v.get("plate",""))
    rows=[]
    for item in debt_sources(con):
        t=item["table"]; cs=cols(con,t)
        apt=first_col(cs,["apartment_number","apartment","unit_number","number"])
        amount=first_col(cs,["amount","debt","debt_amount","balance","sum","total"])
        if not apt or not amount: continue
        try:
            for r in con.execute(f"select {q(apt)} apartment,{q(amount)} amount from {q(t)}"):
                try: val=float(r["amount"] or 0)
                except Exception: val=0
                if val>0:
                    a=str(r["apartment"] or "").strip()
                    rows.append({"кв":a,"ФИО":find_name_for_apartment(con,a),"авто":", ".join(sorted(set(by_apt.get(a,[])))),"сумма":val,"source_table":t})
        except Exception: pass
    return sorted(rows,key=apt_sort_key),src
def main():
    ap=argparse.ArgumentParser(description="Create best-effort parking debtors report.")
    ap.add_argument("--db",default=None); ap.add_argument("--output",default=None); ap.add_argument("--probe",action="store_true")
    args=ap.parse_args(); db=resolve_db(args.db); con=connect(db)
    try:
        if args.probe:
            print("="*90); print("Possible debt/balance tables"); print("="*90)
            ds=debt_sources(con)
            for d in ds: print(f"{d['table']}\n  {d['columns']}")
            if not ds: print("No obvious debt tables found.")
            return 0
        rows,src=build(con)
    finally: con.close()
    public=[{k:r[k] for k in ["кв","ФИО","авто","сумма"]} for r in rows]
    out=Path(args.output) if args.output else DEFAULT_OUTPUT_DIR/f"PARKING_DEBTORS_REPORT_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"
    if not out.is_absolute(): out=(DEFAULT_OUTPUT_DIR.parent.parent/out).resolve() if str(out).startswith("OSBB") else (DEFAULT_OUTPUT_DIR.parent/out).resolve()
    write_xlsx(out,{"Summary":[{"metric":"rows","value":len(rows)},{"metric":"vehicle_source","value":src},{"metric":"note","value":"best-effort; verify debt source table"}],"Debtors_Public":public,"Internal_All":rows})
    print("="*90); print("OSBB Parking Debtors Report"); print("="*90); print("Rows:",len(rows)); print("Excel:",out)
    if not rows: print("No rows found. Run: python .\\OSBB\\tools\\parking_registry\\debtors_report.py --probe")
    return 0
if __name__=="__main__": raise SystemExit(main())
