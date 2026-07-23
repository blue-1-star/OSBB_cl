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
        # TODO: подтвердить актуальный путь — сейчас взято по аналогии
        # со старым config.py (G:/Prog_secret на Windows).
        if self.os_name == "Windows":
            self.SECRETS_DIR = Path("G:/Prog_secret")
        else:  # Darwin (Mac) и прочие — единообразно
            self.SECRETS_DIR = self.home / "Programming" / "Secrets"

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

        self.DB_FILE = self.DB_DIR / "osbb_test.db"  # это и есть боевая база, несмотря на имя — переименовывать не будем, только путаница

        # ==================================================
        # СОВМЕСТИМОСТЬ СО СТАРЫМ ИНТЕРФЕЙСОМ config.py
        # ==================================================
        # Старый код (ещё не адаптированный) ожидает paths.OSBB_TEST_DB_FILE /
        # paths.OSBB_DB_FILE и глобальную USE_TEST_DB. В OSBB_cl база одна —
        # но вместо правки каждого файла-потребителя (десятки мест) даём
        # мягкие алиасы на ту же самую БД. Тот же принцип, что и db_adapter.py:
        # не переписывать вызывающих, а подставить совместимую прослойку.
        self.OSBB_TEST_DB_FILE = self.DB_FILE
        self.OSBB_DB_FILE = self.DB_FILE

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
