from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path
import re

def apartment_sort_key(row):
    apt = str(row.get("apartment") or "").strip()
    try:
        return (0, int(apt), apt)
    except ValueError:
        return (1, 10**9, apt)


def compact_period(period: str) -> str:
    text = str(period or "").strip()
    if text == "2026-05_2026-06":
        return "05-06_26"
    m = re.fullmatch(r"(\d{4})-(\d{2})[_\-](\d{4})-(\d{2})", text)
    if m and m.group(1) == m.group(3):
        return f"{m.group(2)}-{m.group(4)}_{m.group(1)[2:]}"
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})\.(\d{4})", text)
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}_{m.group(3)[2:]}"
    return text

try:
    from ._common import connect, resolve_db, vehicle_registry, payments, is_parking_payment, classify_payment, value, payment_period, DEFAULT_OUTPUT_DIR
    from .excel_export import write_xlsx
except ImportError:
    from _common import connect, resolve_db, vehicle_registry, payments, is_parking_payment, classify_payment, value, payment_period, DEFAULT_OUTPUT_DIR
    from excel_export import write_xlsx

def main():
    ap=argparse.ArgumentParser(description="Create OSBB Parking Registry Report.")
    ap.add_argument("--db",default=None); ap.add_argument("--output",default=None)
    ap.add_argument("--include-all",action="store_true"); ap.add_argument("--title-period",default="05-06.2026")
    ap.add_argument("--night-amount",type=float); ap.add_argument("--day-amount",type=float)
    args=ap.parse_args()
    db=resolve_db(args.db); c=connect(db)
    try:
        vehicles,src=vehicle_registry(c)
        pay_by_apt={}
        for p in payments(c):
            if not is_parking_payment(p,args.include_all): continue
            apt=str(value(p,"apartment_number") or "").strip()
            if apt: pay_by_apt.setdefault(apt,[]).append(p)
    finally:
        c.close()
    rows=[]
    for v in vehicles:
        apt=str(v.get("apartment") or "").strip(); plist=pay_by_apt.get(apt,[])
        latest=plist[0] if plist else None
        tariff=reason=amount=period=ref=""
        if latest:
            tariff,reason=classify_payment(latest,args.night_amount,args.day_amount)
            amount=value(latest,"amount",""); period=compact_period(payment_period(latest,args.title_period)); ref=value(latest,"source_ref") or value(latest,"source") or ""
        pt=str(v.get("parking_time") or "").strip()
        status="OK"
        if not pt and latest: status="MISSING parking_time, payment exists"
        elif pt and tariff and pt.lower()!=tariff.lower(): status="CONFLICT parking_time vs payment"
        elif not latest: status="NO"
        rows.append({"apartment":apt,"plate":v.get("plate",""),"parking_time":pt,"paid_period":period,"paid_tariff":tariff,"amount":amount,"status":status,"vehicle_source":src,"payment_source_ref":ref,"inference_reason":reason,"vehicle_id":v.get("vehicle_id",""),"model":v.get("model",""),"color":v.get("color","")})
    rows=sorted(rows, key=apartment_sort_key)
    public=[{k:r[k] for k in ["apartment","plate","parking_time","paid_period","paid_tariff","amount","status"]} for r in rows]
    missing=[r for r in rows if r["status"].startswith("MISSING")]
    conflicts=[r for r in rows if r["status"].startswith("CONFLICT")]
    no_payment=[r for r in rows if r["status"]=="NO"]
    summary=[{"metric":"vehicle_source","value":src},{"metric":"vehicles","value":len(rows)},{"metric":"missing_parking_time_with_payment","value":len(missing)},{"metric":"tariff_conflicts","value":len(conflicts)},{"metric":"vehicles_without_payment","value":len(no_payment)},{"metric":"title_period","value":compact_period(args.title_period)}]
    out=Path(args.output) if args.output else DEFAULT_OUTPUT_DIR/f"PARKING_REGISTRY_REPORT_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"
    if not out.is_absolute(): out=(DEFAULT_OUTPUT_DIR.parent.parent/out).resolve() if str(out).startswith("OSBB") else (DEFAULT_OUTPUT_DIR.parent/out).resolve()
    write_xlsx(out,{"Summary":summary,"Registry_Public":public,"Missing_Parking_Time":missing,"Tariff_Conflicts":conflicts,"No_Payment":no_payment,"Internal_All":rows})
    print("="*90); print("OSBB Parking Registry Report"); print("="*90)
    print("DB:",db); print("Vehicle source:",src); print("Vehicles:",len(rows)); print("Missing parking_time with payment:",len(missing)); print("Tariff conflicts:",len(conflicts)); print("Vehicles without payment:",len(no_payment)); print("Excel:",out)
    return 0
if __name__=="__main__": raise SystemExit(main())
