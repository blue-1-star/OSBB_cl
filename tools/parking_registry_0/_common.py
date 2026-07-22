from __future__ import annotations
import re, sqlite3
from pathlib import Path
from typing import Any

PROJECT_ROOT=Path(__file__).resolve().parents[2]
DEFAULT_DB=PROJECT_ROOT/"Data"/"db"/"osbb_test.db"
DEFAULT_OUTPUT_DIR=PROJECT_ROOT/"Recovered"
NIGHT_RE=re.compile(r"(ноч|ніч|night|нічн|ночн)",re.I)
DAY_RE=re.compile(r"(днев|денн|день|денний|day)",re.I)
PARKING_RE=re.compile(r"(парков|парку|parking|стоян|машином|авто|vehicle)",re.I)

def q(n:str)->str: return '"'+n.replace('"','""')+'"'
def connect(p:Path): 
    c=sqlite3.connect(str(p)); c.row_factory=sqlite3.Row; return c
def table_exists(c,t): return c.execute("select 1 from sqlite_master where type='table' and name=?",(t,)).fetchone() is not None
def cols(c,t): return [r["name"] for r in c.execute(f"pragma table_info({q(t)})")]
def first_col(cols,names):
    low={x.lower():x for x in cols}
    for n in names:
        if n.lower() in low: return low[n.lower()]
    return None
def value(r,k,d=None): return r[k] if k in r.keys() else d
def resolve_db(s=None):
    if not s: return DEFAULT_DB
    p=Path(s)
    if p.is_absolute(): return p
    return (PROJECT_ROOT.parent/p).resolve() if str(p).startswith("OSBB") else (PROJECT_ROOT/p).resolve()

def apartments_by_id(c):
    out={}
    for t in ("apartments","units","premises"):
        if not table_exists(c,t): continue
        cs=cols(c,t); idc=first_col(cs,["id","apartment_id","unit_id","premise_id"]); aptc=first_col(cs,["apartment_number","unit_number","premise_code","unit_code","number","apartment"])
        if not idc or not aptc: continue
        for r in c.execute(f"select {q(idc)} idv,{q(aptc)} apt from {q(t)}"):
            if r["idv"] is not None and r["apt"]: out[str(r["idv"])]=str(r["apt"]).strip()
    return out

def vehicle_registry(c):
    amap=apartments_by_id(c)
    for t in ("vehicles","vehicle_registry","resident_vehicles"):
        if not table_exists(c,t): continue
        cs=cols(c,t)
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
        for r in c.execute(f"select {', '.join(parts)} from {q(t)}"):
            a=str(r["apartment"] or "").strip()
            if not a and r["apartment_id"] is not None: a=amap.get(str(r["apartment_id"]),"")
            rows.append({"vehicle_id":r["vehicle_id"],"apartment":a,"plate":str(r["plate"] or "").strip(),"parking_time":str(r["parking_time"] or "").strip(),"status":str(r["status"] or "").strip(),"model":str(r["model"] or "").strip(),"color":str(r["color"] or "").strip(),"source_table":t})
        return rows,t
    return [],"NOT FOUND"

def payments(c):
    if not table_exists(c,"payments"): return []
    cs=cols(c,"payments"); order="id" if "id" in cs else cs[0]
    return c.execute(f"select * from payments order by {q(order)} desc").fetchall()

def payment_text(r): return " ".join(str(value(r,k) or "") for k in ["comment","service_item_code","base_service_code","service_type","source","source_ref","period_code"])
def is_parking_payment(r,include_all): return True if include_all else bool(PARKING_RE.search(payment_text(r)))
def classify_payment(r,night_amount=None,day_amount=None):
    txt=payment_text(r)
    try: amount=float(value(r,"amount"))
    except Exception: amount=None
    if NIGHT_RE.search(txt): return "Night","text marker"
    if DAY_RE.search(txt): return "Day","text marker"
    if night_amount is not None and amount==night_amount: return "Night",f"amount={night_amount}"
    if day_amount is not None and amount==day_amount: return "Day",f"amount={day_amount}"
    return "","undefined"
def payment_period(r,default): return str(value(r,"period_code") or default or "").strip()
