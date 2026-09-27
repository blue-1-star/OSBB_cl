"""Canonical OSBB document layout; portable references and lazy directories.

The first backend is a filesystem (including mounted or synced cloud storage).
Cloud object-storage APIs need a separate adapter, not an invented local path.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import argparse
import json
import re
from config import paths

STORAGE_KEY = 'OSBB_DOCUMENTS'

@dataclass(frozen=True)
class Section:
    directory: str
    description: str
    annual: bool = False
    entity_prefix: str | None = None
    folders: tuple[str, ...] = ()

# Single source of truth; folders are created only when explicitly requested.
SECTIONS = {
    'governance': Section('01_Governance','Устав, собрания, протоколы, решения'),
    'property': Section('02_Property','Дом, помещения, планы, техпаспорта'),
    'counterparties': Section('03_Counterparties','Контрагенты',entity_prefix='Supplier',folders=('Contracts','Correspondence')),
    'procurement': Section('04_Procurement','Закупки',annual=True,entity_prefix='PO',folders=('Order','Invoice','Delivery','Correspondence')),
    'finance': Section('05_Finance','Финансовые документы',annual=True,folders=('Bank','Cash','Reports')),
    'residents': Section('06_Residents','Анкеты, заявления, согласия'),
    'parking': Section('07_Parking','Парковка',folders=('Recognition','Reports')),
    'operations': Section('08_Operations','Обслуживание, ремонты, обследования'),
    'announcements': Section('09_Announcements','Объявления для печати и расклейки',folders=('Print',)),
    'inbox': Section('99_Inbox','Документы до регистрации'),
}

def relative_directory(section: str, *, year: int | None = None, entity: str | int | None = None,
                       folder: str | None = None) -> str:
    if section not in SECTIONS: raise ValueError('Неизвестный раздел документов.')
    spec = SECTIONS[section]
    parts = [spec.directory]
    if spec.annual:
        if not isinstance(year,int) or isinstance(year,bool) or not 1900 <= year <= 9999:
            raise ValueError('Для раздела нужен год (1900–9999).')
        parts.append(str(year))
    elif year is not None: raise ValueError('В этом разделе год не предусмотрен.')
    if spec.entity_prefix:
        value = str(entity or '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value):
            raise ValueError('Нужен безопасный код сущности: буквы, цифры, _ и -.')
        parts.append(f'{spec.entity_prefix}_{value}')
    elif entity is not None: raise ValueError('В этом разделе код сущности не предусмотрен.')
    if folder:
        if folder not in spec.folders: raise ValueError('Неизвестный подкаталог раздела.')
        parts.append(folder)
    return PurePosixPath(*parts).as_posix()

def document_reference(section: str, *, filename: str, **kwargs) -> dict[str,str]:
    # Conservative portable filename rules: reject Windows and POSIX traversal.
    if (not filename or filename in {'.','..'} or filename.endswith((' ','.'))
        or re.search(r'[\\/:*?"<>|\x00-\x1f]',filename)
        or re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',filename)):
        raise ValueError('Недопустимое имя файла для Mac/Windows.')
    return {'storage':STORAGE_KEY,'path':f'{relative_directory(section,**kwargs)}/{filename}'}

def resolve_reference(reference: dict[str,str], *, root: Path | None = None) -> Path:
    if reference.get('storage') != STORAGE_KEY: raise ValueError('Неизвестное хранилище.')
    configured = root if root is not None else paths.OSBB_DOCUMENTS_ROOT
    if configured is None: raise RuntimeError('Задайте OSBB_DOCUMENTS_ROOT в конфигурации окружения.')
    base = Path(configured)
    if not base.is_absolute(): raise ValueError('Корень хранилища должен быть абсолютным.')
    if not base.is_dir(): raise FileNotFoundError('Хранилище недоступно. Подключите диск/том; корень автоматически не создаётся.')
    value = reference.get('path','')
    relative = PurePosixPath(value)
    if not value or '\\' in value or ':' in value or relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Недопустимый относительный путь.')
    base = base.resolve()
    target = base.joinpath(*relative.parts).resolve()
    if not target.is_relative_to(base): raise ValueError('Путь выходит за пределы хранилища.')
    return target

def ensure_directory(section: str, *, root: Path | None = None, **kwargs) -> Path:
    relative = relative_directory(section,**kwargs)
    target = resolve_reference({'storage':STORAGE_KEY,'path':relative},root=root)
    target.mkdir(parents=True,exist_ok=True)
    return target

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--section',choices=SECTIONS)
    parser.add_argument('--year',type=int)
    parser.add_argument('--entity')
    parser.add_argument('--folder')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if not args.section:
        print(json.dumps({key:vars(spec) for key,spec in SECTIONS.items()},ensure_ascii=False,indent=2)); return
    kwargs={'year':args.year,'entity':args.entity,'folder':args.folder}
    print(relative_directory(args.section,**kwargs))
    if args.apply: print(ensure_directory(args.section,**kwargs))
    else: print('Только план. Для создания используйте --apply.')

if __name__=='__main__': main()
