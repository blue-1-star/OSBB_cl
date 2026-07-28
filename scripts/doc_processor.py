#!/usr/bin/env python
"""
Документатор — обрабатывает входящие файлы и обновляет основную документацию.
При первом запуске создаёт всю необходимую структуру.
"""

import os
import shutil
import zipfile
from pathlib import Path
from datetime import datetime

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================

BASE_DIR = Path("G:/Programming/OSBB_cl")
DOCS_DIR = BASE_DIR / "docs"
INCOMING_DIR = DOCS_DIR / "_incoming"
ARC_DIR = BASE_DIR / "arc_docs"
SCRIPTS_DIR = BASE_DIR / "scripts"

# Файлы, которые мы умеем обрабатывать
KNOWN_FILES = {
    "Project_Log.md": DOCS_DIR / "Project_Log.md",
    "ROADMAP.md": DOCS_DIR / "ROADMAP.md",
}


# ==========================================
# ПРОВЕРКА И СОЗДАНИЕ СТРУКТУРЫ
# ==========================================

def ensure_structure():
    """
    Проверяет наличие всех папок и создаёт их при необходимости.
    При первом запуске также создаёт README.md и базовые файлы.
    """
    print("📁 Проверка структуры каталогов...")

    # Создаём папки
    for folder in [DOCS_DIR, INCOMING_DIR, ARC_DIR, SCRIPTS_DIR]:
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)
            print(f"  ✅ Создана папка: {folder}")

    # Если папка docs пустая — создаём базовые файлы
    if not any(DOCS_DIR.glob("*.md")):
        print("  📄 Создаю базовые документы...")

        # README.md
        readme_path = DOCS_DIR / "README.md"
        readme_path.write_text("""# Документация OSBB

Это центральное хранилище документации проекта OSBB.

## Основные документы
- [Project_Log.md](Project_Log.md) — ежедневный журнал проекта.
- [ROADMAP.md](ROADMAP.md) — план развития.

## Как обновлять
1. Положи новый файл в папку `_incoming/`.
2. Запусти `python scripts/doc_processor.py`.
3. Скрипт сам добавит содержимое в нужный документ и заархивирует исходник.
""", encoding='utf-8')
        print(f"    ✅ Создан: {readme_path}")

        # Project_Log.md
        log_path = DOCS_DIR / "Project_Log.md"
        log_path.write_text("""# Project Log

Журнал ежедневных изменений и решений по проекту OSBB.

---
""", encoding='utf-8')
        print(f"    ✅ Создан: {log_path}")

        # ROADMAP.md
        roadmap_path = DOCS_DIR / "ROADMAP.md"
        roadmap_path.write_text("""# ROADMAP

План развития проекта OSBB.

---
""", encoding='utf-8')
        print(f"    ✅ Создан: {roadmap_path}")

    # Создаём скрипт-обёртку для удобного запуска (если нет)
    runner_path = SCRIPTS_DIR / "update_docs.bat"
    if not runner_path.exists():
        runner_path.write_text("""@echo off
echo 📄 Обновление документации OSBB...
python "%~dp0doc_processor.py"
pause
""", encoding='utf-8')
        print(f"    ✅ Создан: {runner_path}")

    print("  ✅ Структура готова.\n")


# ==========================================
# ОСНОВНАЯ ЛОГИКА
# ==========================================

def append_to_file(source: Path, target: Path):
    """Дозаписывает содержимое source в конец target."""
    if not source.exists():
        print(f"⚠️ Файл не найден: {source}")
        return False

    content = source.read_text(encoding='utf-8')
    if target.exists():
        with open(target, 'a', encoding='utf-8') as f:
            f.write("\n\n" + content)
    else:
        target.write_text(content, encoding='utf-8')

    print(f"✅ Добавлено в {target.name}")
    return True


def archive_file(filepath: Path):
    """
    Перемещает файл в arc_docs и добавляет в ZIP-архив с уникальным именем.
    К имени файла добавляется дата и время.
    """
    if not filepath.exists():
        return

    zip_name = f"archive_{datetime.now().strftime('%Y%m%d')}.zip"
    zip_path = ARC_DIR / zip_name

    # Формируем уникальное имя: Project_Log_20260726_143052.md
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    arcname = f"{filepath.stem}_{timestamp}{filepath.suffix}"

    # Временно копируем файл в arc_docs с новым именем
    temp_path = ARC_DIR / arcname
    shutil.copy2(filepath, temp_path)

    # Добавляем в ZIP
    with zipfile.ZipFile(zip_path, 'a', zipfile.ZIP_DEFLATED) as zf:
        zf.write(temp_path, arcname=arcname)

    # Удаляем временный файл
    temp_path.unlink()

    # Удаляем исходный файл из _incoming
    filepath.unlink()

    print(f"📦 Архивирован: {filepath.name} -> {zip_name} (как {arcname})")


def get_target(filepath: Path) -> Path | None:
    """Определяет, в какой документ нужно дозаписать файл."""
    name = filepath.name.lower()
    if "project_log" in name:
        return DOCS_DIR / "Project_Log.md"
    if "roadmap" in name:
        return DOCS_DIR / "ROADMAP.md"
    return None


def process_incoming():
    """Основной процесс обработки входящих файлов."""
    print("=" * 60)
    print("📄 Документатор OSBB")
    print("=" * 60)

    incoming_files = list(INCOMING_DIR.glob("*.md"))
    if not incoming_files:
        print("ℹ️ Нет входящих файлов.")
        return

    for filepath in incoming_files:
        target = get_target(filepath)
        if target is None:
            print(f"⚠️ Неизвестный файл: {filepath.name}")
            continue

        if append_to_file(filepath, target):
            archive_file(filepath)
# ==========================================
# ЗАПУСК
# ==========================================

if __name__ == "__main__":
    ensure_structure()
    process_incoming()