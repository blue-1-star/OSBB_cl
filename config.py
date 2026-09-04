# config.py (в корне OSBB_cl/)
#
# Кроссплатформенный конфиг путей, выделенный только под OSBB_cl.
# Структура ЗЕРКАЛЬНА старому OSBB (Bots/, tools/, core_new/, Data/, Docs/) —
# осознанное решение: минимум риска сломать импорты при переносе.
# Реорганизация по подсистемам (presentation/, business_core/ и т.п.) —
# отдельная, постепенная задача на потом, когда всё уже работает.

import sys
import platform
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


class ProjectPaths:
    def __init__(self):
        self.os_name = platform.system()
        self.home = Path.home()
        self.PROJECT_ROOT = PROJECT_ROOT

        # ==================================================
        # СЕКРЕТЫ (токен телеграм-бота и т.п.) — вне репозитория
        # ==================================================
        # Windows: G:\Prog_secret
        # Mac: отдельный физический раздел "Secret" (/Volumes/Secret),
        #      подтверждено фактическим расположением файла на 2026-08-16 —
        #      /Volumes/Secret/Soft/telegram_osbb.py. НЕ ~/Programming/Secrets —
        #      той папки на Маке больше нет (структура ~/Programming
        #      осталась только на Windows-машине).
        if self.os_name == "Windows":
            self.SECRETS_DIR = Path("G:/Prog_secret")
        elif self.os_name == "Darwin":
            self.SECRETS_DIR = Path("/Volumes/Secret/Soft")
        else:  # прочие ОС — пока не встречались, безопасный дефолт рядом с проектом
            self.SECRETS_DIR = self.home / "Secrets"

        self.TELEGRAM_SECRETS_FILE = self.SECRETS_DIR / "telegram_osbb.py"

        # ==================================================
        # ДАННЫЕ — имена папок зеркальны старому OSBB
        # ==================================================
        self.DATA_DIR = self.PROJECT_ROOT / "Data"
        self.RAW_DIR = self.DATA_DIR / "raw"
        self.TYPED_DIR = self.RAW_DIR / "typed"
        self.DB_DIR = self.DATA_DIR / "db"
        self.EXPORTS_DIR = self.DATA_DIR / "exports"
        self.LOGS_DIR = self.DATA_DIR / "logs"
        self.BACKUPS_DIR = self.DB_DIR / "backups"
        self.TELEGRAM_RAW_DIR = self.RAW_DIR / "telegram"

        self.DB_FILE = self.DB_DIR / "osbb_test.db"  # это и есть боевая база, несмотря на имя — переименовывать не будем, только путаница

        # ==================================================
        # СОВМЕСТИМОСТЬ СО СТАРЫМ ИНТЕРФЕЙСОМ config.py
        # ==================================================
        # Старый код по всему дереву (не только db_access.py/access_control.py)
        # может обращаться к paths.OSBB_* напрямую — это был полный список
        # атрибутов в исходном config.py проекта. Правим все разом одним
        # блоком, а не по одному при каждом новом крэше на очередном файле.
        # Тот же принцип, что и db_adapter.py: не переписывать вызывающих,
        # а подставить совместимую прослойку.
        self.OSBB_ROOT = self.PROJECT_ROOT
        self.OSBB_DATA_DIR = self.DATA_DIR
        self.OSBB_RAW_DIR = self.RAW_DIR
        self.OSBB_TYPED_DIR = self.TYPED_DIR
        self.OSBB_DB_DIR = self.DB_DIR
        self.OSBB_EXPORTS_DIR = self.EXPORTS_DIR
        self.OSBB_LOGS_DIR = self.LOGS_DIR
        self.OSBB_BACKUPS_DIR = self.BACKUPS_DIR
        self.OSBB_DB_FILE = self.DB_FILE
        self.OSBB_TEST_DB_FILE = self.DB_FILE
        self.OSBB_HOUSE_REGISTRY_FILE = self.DATA_DIR / "24А ПОЛНЫЙ СПИСОК Сервис Житлобуд1 - Copy.xlsx"
        self.OSBB_PAPER_PARKING_FILE = self.TYPED_DIR / "OSBB_Base_Cleaned_06_06.xlsx"
        self.OSBB_TBOT_PARKING_FILE = self.TYPED_DIR / "parking_tbot2.xlsx"
        self.OSBB_AUDIT_REPORT_FILE = self.TYPED_DIR / "DATABASE_AUDIT_REPORT_06_06.txt"
        self.OSBB_QUARANTINE_DB_FILE = self.DB_DIR / "osbb_quarantine.db"
        self.OSBB_TELEGRAM_DB_FILE = self.DB_DIR / "osbb_telegram.db"
        self.OSBB_TELEGRAM_RAW_DIR = self.TELEGRAM_RAW_DIR

        # ==================================================
        # ДОКУМЕНТАЦИЯ
        # ==================================================
        self.DOCS_DIR = self.PROJECT_ROOT / "Docs"

    def ensure_directories(self):
        """Создаёт недостающие рабочие папки (не трогает git/секреты)."""
        dirs = [
            self.DATA_DIR,
            self.RAW_DIR,
            self.TYPED_DIR,
            self.DB_DIR,
            self.EXPORTS_DIR,
            self.LOGS_DIR,
            self.BACKUPS_DIR,
            self.TELEGRAM_RAW_DIR,
            self.DOCS_DIR,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


# ✅ ЕДИНЫЙ ЭКЗЕМПЛЯР — как и в старом config.py
paths = ProjectPaths()

# Совместимость: старый код делает "from config import paths, USE_TEST_DB".
# В OSBB_cl база одна (paths.DB_FILE), но само имя оставляем существующим,
# чтобы не редактировать каждого потребителя. Значение не влияет на то,
# какая БД используется — она одна и та же в любом случае.
USE_TEST_DB = True