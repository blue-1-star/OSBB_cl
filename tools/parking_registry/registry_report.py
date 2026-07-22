from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path
try:
    from ._common import *
    from .period_tools import compact_period
    from .excel_export import write_xlsx
except ImportError:
    from _common import *
    from period_tools import compact_period
    from excel_export import write_xlsx

def main():
    ap=argparse.ArgumentParser(description="Create OSBB Parking Registry Report.")
    ap.add_argument("--db",default=None); ap.add_argument("--output",default=None)
    ap.add_argument("--include-all",action="store_true"); ap.add_argument("--title-period",default="05-06.2026")
    ap.add_argument("--night-amount",type=float); ap.add_argument("--day-amount",type=float)
    args=ap.parse_args(); db=resolve_db(args.db); con=connect(db)
    try:
        vehicles,src=vehicle_registry(con); pay_by_apt={}
        for p in payments(con):
            if is_parking_payment(p,args.include_all):
                apt=str(value(p,"apartment_number") or "").strip()
                if apt: pay_by_apt.setdefault(apt,[]).append(p)
    finally: con.close()
    rows=[]
    for v in vehicles:
        apt=str(v.get("apartment") or "").strip(); latest=(pay_by_apt.get(apt) or [None])[0]
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
    rows=sorted(rows,key=apt_sort_key)
    public=[{k:r[k] for k in ["apartment","plate","parking_time","paid_period","paid_tariff","amount","status"]} for r in rows]
    missing=[r for r in rows if r["status"].startswith("MISSING")]
    conflicts=[r for r in rows if r["status"].startswith("CONFLICT")]
    no_payment=[r for r in rows if r["status"]=="NO"]
    summary=[{"metric":"vehicle_source","value":src},{"metric":"vehicles","value":len(rows)},{"metric":"missing_parking_time_with_payment","value":len(missing)},{"metric":"tariff_conflicts","value":len(conflicts)},{"metric":"vehicles_without_payment","value":len(no_payment)},{"metric":"title_period","value":compact_period(args.title_period)}]
    out=Path(args.output) if args.output else DEFAULT_OUTPUT_DIR/f"PARKING_REGISTRY_REPORT_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"
    if not out.is_absolute(): out=(DEFAULT_OUTPUT_DIR.parent.parent/out).resolve() if str(out).startswith("OSBB") else (DEFAULT_OUTPUT_DIR.parent/out).resolve()
    write_xlsx(out,{"Summary":summary,"Registry_Public":public,"Missing_Parking_Time":missing,"Tariff_Conflicts":conflicts,"No_Payment":no_payment,"Internal_All":rows})
    print("="*90); print("OSBB Parking Registry Report"); print("="*90)
    print("Excel:",out)
    return 0
if __name__=="__main__": raise SystemExit(main())
