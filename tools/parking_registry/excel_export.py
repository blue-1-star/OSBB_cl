from __future__ import annotations
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

def col(n:int)->str:
    s=""
    while n:
        n,r=divmod(n-1,26); s=chr(65+r)+s
    return s

def sheet(name:str)->str:
    return "".join("_" if c in '[]:*?/\\' else c for c in name)[:31] or "Sheet"

def write_xlsx(path:Path, sheets:dict[str,list[dict[str,Any]]])->None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names=list(sheets)
    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(names)+1))+'</Types>')
        z.writestr("_rels/.rels",'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="{escape(sheet(n))}" sheetId="{i}" r:id="rId{i}"/>' for i,n in enumerate(names,1))+'</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(names)+1))+'</Relationships>')
        for i,n in enumerate(names,1):
            rows=sheets[n]; headers=list(rows[0]) if rows else ["message"]; data=rows if rows else [{"message":"No rows"}]
            xml=[]
            for rix,row in enumerate([dict.fromkeys(headers,None)]+data,1):
                cells=[]
                for cix,h in enumerate(headers,1):
                    val=h if rix==1 else row.get(h,""); ref=f"{col(cix)}{rix}"
                    if isinstance(val,(int,float)) and not isinstance(val,bool): cells.append(f'<c r="{ref}"><v>{val}</v></c>')
                    else: cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(val or ""))}</t></is></c>')
                xml.append(f'<row r="{rix}">'+''.join(cells)+'</row>')
            z.writestr(f"xl/worksheets/sheet{i}.xml",'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>'+''.join(xml)+'</sheetData></worksheet>')
