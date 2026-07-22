# config.py (в корне OSBB_cl/)
#
# Кроссплатформенный конфиг путей, но, в отличие от старого общего
# config.py в Py/, этот — выделенный, только под нужды OSBB_cl.
# Никакого Music/Flat/чужих проектов здесь нет и не будет.

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
        # ДАННЫЕ
        # ==================================================
        self.DATA_DIR = self.PROJECT_ROOT / "data"
        self.DB_DIR = self.DATA_DIR / "db"
        self.DB_FILE = self.DB_DIR / "osbb.db"          # бывший osbb_test.db
        self.LOGS_DIR = self.DATA_DIR / "logs"
        self.EXPORTS_DIR = self.DATA_DIR / "exports"      # для отчётов dev_infrastructure

        # ==================================================
        # ПОДСИСТЕМЫ (для удобных импортов и диагностики)
        # ==================================================
        self.CORE_NEW_DIR = self.PROJECT_ROOT / "core_new"
        self.FINANCE_CORE_DIR = self.PROJECT_ROOT / "finance_core"
        self.PRESENTATION_DIR = self.PROJECT_ROOT / "presentation"
        self.DEV_INFRA_DIR = self.PROJECT_ROOT / "dev_infrastructure"

        # ==================================================
        # ДОКУМЕНТАЦИЯ
        # ==================================================
        self.DOCS_DIR = self.PROJECT_ROOT / "docs"

    def ensure_directories(self):
        """Создаёт недостающие рабочие папки (не трогает git/секреты)."""
        dirs = [
            self.DATA_DIR,
            self.DB_DIR,
            self.LOGS_DIR,
            self.EXPORTS_DIR,
            self.DOCS_DIR,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


# ✅ ЕДИНЫЙ ЭКЗЕМПЛЯР — как и в старом config.py
paths = ProjectPaths()
