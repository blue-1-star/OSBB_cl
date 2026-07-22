from __future__ import annotations
import re, sqlite3
from pathlib import Path

PROJECT_ROOT=Path(__file__).resolve().parents[2]
DEFAULT_DB=PROJECT_ROOT/"Data"/"db"/"osbb_test.db"
DEFAULT_OUTPUT_DIR=PROJECT_ROOT/"Recovered"
NIGHT_RE=re.compile(r"(ноч|ніч|night|нічн|ночн)",re.I)
DAY_RE=re.compile(r"(днев|денн|день|денний|day)",re.I)
PARKING_RE=re.compile(r"(парков|парку|parking|стоян|машином|авто|vehicle)",re.I)

def q(n): return '"'+str(n).replace('"','""')+'"'
def connect(p):
    con=sqlite3.connect(str(p)); con.row_factory=sqlite3.Row; return con
def table_exists(con,t): return con.execute("select 1 from sqlite_master where type='table' and name=?",(t,)).fetchone() is not None
def table_names(con): return [r["name"] for r in con.execute("select name from sqlite_master where type='table' order by name")]
def cols(con,t): return [r["name"] for r in con.execute(f"pragma table_info({q(t)})")]
def first_col(cs,names):
    low={c.lower():c for c in cs}
    for n in names:
        if n.lower() in low: return low[n.lower()]
    return None
def value(row,key,default=None): return row[key] if key in row.keys() else default
def resolve_db(text=None):
    if not text: return DEFAULT_DB
    p=Path(text)
    if p.is_absolute(): return p
    return (PROJECT_ROOT.parent/p).resolve() if str(p).startswith("OSBB") else (PROJECT_ROOT/p).resolve()
def apt_sort_key(row):
    apt=str(row.get("apartment") or row.get("apartment_number") or row.get("кв") or "").strip()
    try: return (0,int(apt),apt)
    except ValueError: return (1,10**9,apt)
def apartments_by_id(con):
    out={}
    for t in ("apartments","units","premises"):
        if not table_exists(con,t): continue
        cs=cols(con,t); idc=first_col(cs,["id","apartment_id","unit_id","premise_id"]); aptc=first_col(cs,["apartment_number","unit_number","premise_code","unit_code","number","apartment"])
        if not idc or not aptc: continue
        for r in con.execute(f"select {q(idc)} idv,{q(aptc)} apt from {q(t)}"):
            if r["idv"] is not None and r["apt"]: out[str(r["idv"])]=str(r["apt"]).strip()
    return out
def vehicle_registry(con):
    amap=apartments_by_id(con)
    for t in ("vehicles","vehicle_registry","resident_vehicles"):
        if not table_exists(con,t): continue
        cs=cols(con,t)
        plate=first_col(cs,["license_plate","plate","vehicle_plate","plate_number","car_number","number"])
        apt=first_col(cs,["apartment_number","apartment","unit_number"])
        aptid=first_col(cs,["apartment_id","unit_id","premise_id"])
        parking=first_col(cs,["parking_time","parking_mode","parking_type"])
        status=first_col(cs,["status","is_active"])
        model=first_col(cs,["car_model","model","vehicle_model"])
        color=first_col(cs,["car_color","color","vehicle_color"])
        vid=first_col(cs,["id"])
        if not plate: continue
        parts=[f"{q(vid)} vehicle_id" if vid else "null vehicle_id", f"{q(plate)} plate", f"{q(apt)} apartment" if apt else "null apartment", f"{q(aptid)} apartment_id" if aptid else "null apartment_id", f"{q(parking)} parking_time" if parking else "null parking_time", f"{q(status)} status" if status else "null status", f"{q(model)} model" if model else "null model", f"{q(color)} color" if color else "null color"]
        rows=[]
        for r in con.execute(f"select {', '.join(parts)} from {q(t)}"):
            a=str(r["apartment"] or "").strip()
            if not a and r["apartment_id"] is not None: a=amap.get(str(r["apartment_id"]),"")
            rows.append({"vehicle_id":r["vehicle_id"],"apartment":a,"plate":str(r["plate"] or "").strip(),"parking_time":str(r["parking_time"] or "").strip(),"status":str(r["status"] or "").strip(),"model":str(r["model"] or "").strip(),"color":str(r["color"] or "").strip(),"source_table":t})
        return rows,t
    return [],"NOT FOUND"
def payments(con):
    if not table_exists(con,"payments"): return []
    cs=cols(con,"payments"); order="id" if "id" in cs else cs[0]
    return con.execute(f"select * from payments order by {q(order)} desc").fetchall()
def payment_text(row): return " ".join(str(value(row,k) or "") for k in ["comment","service_item_code","base_service_code","service_type","source","source_ref","period_code"])
def is_parking_payment(row,include_all): return True if include_all else bool(PARKING_RE.search(payment_text(row)))
def classify_payment(row,night_amount=None,day_amount=None):
    txt=payment_text(row)
    try: amount=float(value(row,"amount"))
    except Exception: amount=None
    if NIGHT_RE.search(txt): return "Night","text marker"
    if DAY_RE.search(txt): return "Day","text marker"
    if night_amount is not None and amount==night_amount: return "Night",f"amount={night_amount}"
    if day_amount is not None and amount==day_amount: return "Day",f"amount={day_amount}"
    return "","undefined"
def payment_period(row,default): return str(value(row,"period_code") or default or "").strip()
def find_name_for_apartment(con,apt):
    for t in ("residents","resident_profiles","users","apartment_residents"):
        if not table_exists(con,t): continue
        cs=cols(con,t); ac=first_col(cs,["apartment_number","apartment","unit_number"]); nc=first_col(cs,["full_name","name","display_name","owner_name","resident_name"])
        if ac and nc:
            r=con.execute(f"select {q(nc)} n from {q(t)} where {q(ac)}=? limit 1",(apt,)).fetchone()
            if r and r["n"]: return str(r["n"])
    return ""
