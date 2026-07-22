from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
try:
    from ._common import connect, resolve_db, vehicle_registry, DEFAULT_OUTPUT_DIR
    from .excel_export import write_xlsx
except ImportError:
    from _common import connect, resolve_db, vehicle_registry, DEFAULT_OUTPUT_DIR
    from excel_export import write_xlsx

def main():
    ap=argparse.ArgumentParser(description="Audit OSBB parking registry quality.")
    ap.add_argument("--db",default=None); ap.add_argument("--output",default=None); args=ap.parse_args()
    db=resolve_db(args.db); c=connect(db)
    try: vehicles,src=vehicle_registry(c)
    finally: c.close()
    by=defaultdict(list)
    for v in vehicles:
        p=str(v.get("plate") or "").strip().upper()
        if p: by[p].append(v)
    dup=[{"plate":p,"count":len(items),"apartments":", ".join(sorted(set(str(x.get("apartment") or "") for x in items))),"vehicle_ids":", ".join(str(x.get("vehicle_id") or "") for x in items)} for p,items in by.items() if len(items)>1]
    missing=[{"apartment":v.get("apartment",""),"plate":v.get("plate",""),"vehicle_id":v.get("vehicle_id",""),"model":v.get("model",""),"status":v.get("status","")} for v in vehicles if not str(v.get("parking_time") or "").strip()]
    noapt=[{"plate":v.get("plate",""),"vehicle_id":v.get("vehicle_id",""),"parking_time":v.get("parking_time",""),"model":v.get("model","")} for v in vehicles if not str(v.get("apartment") or "").strip()]
    allowed={"day","night","ден","ноч"}
    invalid=[{"apartment":v.get("apartment",""),"plate":v.get("plate",""),"parking_time":v.get("parking_time",""),"vehicle_id":v.get("vehicle_id","")} for v in vehicles if str(v.get("parking_time") or "").strip() and str(v.get("parking_time")).lower() not in allowed]
    score=max(0,100-(len(dup)+len(noapt))*10-(len(missing)+len(invalid)))
    summary=[{"metric":"vehicle_source","value":src},{"metric":"vehicles","value":len(vehicles)},{"metric":"health_score","value":score},{"metric":"duplicate_plates","value":len(dup)},{"metric":"missing_parking_time","value":len(missing)},{"metric":"missing_apartment","value":len(noapt)},{"metric":"invalid_parking_time","value":len(invalid)}]
    out=Path(args.output) if args.output else DEFAULT_OUTPUT_DIR/f"PARKING_REGISTRY_AUDIT_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"
    if not out.is_absolute(): out=(DEFAULT_OUTPUT_DIR.parent.parent/out).resolve() if str(out).startswith("OSBB") else (DEFAULT_OUTPUT_DIR.parent/out).resolve()
    write_xlsx(out,{"Summary":summary,"Duplicate_Plates":dup,"Missing_Parking_Time":missing,"Missing_Apartment":noapt,"Invalid_Parking_Time":invalid})
    print("="*90); print("OSBB Parking Registry Audit"); print("="*90)
    print("DB:",db); print("Vehicle source:",src); print("Health score:",score); print("Duplicate plates:",len(dup)); print("Missing parking_time:",len(missing)); print("Missing apartment:",len(noapt)); print("Invalid parking_time:",len(invalid)); print("Excel:",out)
    return 0
if __name__=="__main__": raise SystemExit(main())
