"""Read-only Telegram bot integrity checks; never log or print the API token.

The in-process loop runs while the OSBB bot is running.  The CLI can also be
run separately, so a stopped bot can still be checked without starting it.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config import paths

EXPECTED_USERNAME = "Parking_24a_GS_bot"
EXPECTED_NAME = "Parking 24A GS"
BASELINE_FILE = paths.PROJECT_ROOT / "data" / "security" / "bot_profile_baseline.json"
LOG_FILE = paths.PROJECT_ROOT / "data" / "logs" / "bot_security.log"
CHECK_INTERVAL_SECONDS = 300
KEEP_OK = 2
KEEP_TEST = 2
KEEP_OTHER = 50
_LINK = re.compile(r"(?i)(?:https?://[^\s<>\"']+|t\.me/[^\s<>\"']+|@[A-Za-z][A-Za-z0-9_]{4,})")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_error(exc: Exception, token: str = "") -> str:
    message = str(exc)
    if token:
        message = message.replace(token, "[REDACTED_TOKEN]")
    return message[:500]


def write_event(kind: str, details: dict | None = None, *, log_file: Path = LOG_FILE) -> None:
    """Append an event and automatically prune routine entries."""
    _update_log(log_file, {"at": _utc_now(), "kind": kind, "details": details or {}})


def prune_log(*, log_file: Path = LOG_FILE) -> None:
    """Prune an existing log without inventing a new security check."""
    if log_file.is_file():
        _update_log(log_file)


def _update_log(log_file: Path, event: dict | None = None) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = log_file.with_name(log_file.name + ".lock")
    with lock_path.open("a+b") as lock_handle:
        if os.name == "posix":
            import fcntl
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
        try:
            events = recent_events(log_file=log_file, limit=None)
            if event is not None:
                events.append(event)
            today = _utc_now()[:10]
            normal = [item for item in events if item.get("kind") == "OK"
                      and str(item.get("at", ""))[:10] == today][-KEEP_OK:]
            tests = [item for item in events if item.get("kind") == "SELF_TEST_ALERT"
                     and str(item.get("at", ""))[:10] == today][-KEEP_TEST:]
            other = [item for item in events if item.get("kind") not in {"OK", "SELF_TEST_ALERT"}][-KEEP_OTHER:]
            keep_ids = {id(item) for item in normal + tests + other}
            retained = [item for item in events if id(item) in keep_ids]
            temp_name = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=log_file.parent,
                                                 prefix=".bot_security_", suffix=".tmp", delete=False) as handle:
                    temp_name = handle.name
                    for item in retained:
                        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
                os.replace(temp_name, log_file)
            finally:
                if temp_name and os.path.exists(temp_name):
                    os.unlink(temp_name)
        finally:
            if os.name == "posix":
                fcntl.flock(lock_handle, fcntl.LOCK_UN)


def recent_events(*, log_file: Path = LOG_FILE, limit: int | None = 100) -> list[dict]:
    """Read recent journal entries for the admin UI; skip malformed lines."""
    if not log_file.is_file():
        return []
    with log_file.open(encoding="utf-8") as handle:
        lines = deque(handle, maxlen=limit) if limit is not None else handle.readlines()
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except (ValueError, TypeError):
            continue
    return events


def alert_local(reason: str) -> None:
    """Alert outside the compromised bot; no untrusted text enters AppleScript."""
    print(f"\a🚨 OSBB BOT SECURITY: {reason}", file=sys.stderr, flush=True)
    if platform.system() == "Darwin":
        try:
            subprocess.run(
                ["osascript", "-e", 'display notification "Проверьте журнал bot_security.log" '
                 'with title "OSBB: тревога безопасности бота" sound name "Basso"'],
                check=False, timeout=8, capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


async def snapshot(bot) -> dict:
    """Read public profile and delivery mode, without requesting message updates."""
    me = await bot.get_me()
    name = await bot.get_my_name()
    description = await bot.get_my_description()
    about = await bot.get_my_short_description()
    webhook = await bot.get_webhook_info()
    photos = await bot.get_user_profile_photos(me.id, limit=1)
    commands = await bot.get_my_commands()
    localized = {}
    for language in ("uk", "ru", "en"):
        local_name = await bot.get_my_name(language_code=language)
        local_about = await bot.get_my_short_description(language_code=language)
        local_description = await bot.get_my_description(language_code=language)
        localized[language] = {
            "name": local_name.name or "",
            "about": local_about.short_description or "",
            "description": local_description.description or "",
        }
    photo_id = photos.photos[0][-1].file_unique_id if photos.photos else None
    return {
        "bot_id": me.id,
        "username": me.username or "",
        "name": name.name or "",
        "about": about.short_description or "",
        "description": description.description or "",
        "avatar_file_unique_id": photo_id,
        "commands": [{"command": item.command, "description": item.description} for item in commands],
        "localized": localized,
        "webhook_url": webhook.url or "",
    }


def load_baseline(path: Path = BASELINE_FILE) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _foreign_links(value: str, own_username: str) -> list[str]:
    own = own_username.casefold().lstrip("@")
    links = []
    allowed = {own, f"t.me/{own}", f"https://t.me/{own}", f"http://t.me/{own}"}
    for match in _LINK.finditer(value):
        link = match.group(0).rstrip("/.,;:!?)]}👉")
        if link.casefold().lstrip("@") not in allowed:
            links.append(link)
    return links


def differences(current: dict, baseline: dict | None) -> dict:
    issues: dict[str, object] = {}
    if current["username"].casefold() != EXPECTED_USERNAME.casefold():
        issues["username"] = current["username"]
    if current["name"] != EXPECTED_NAME:
        issues["name"] = current["name"]
    if current["webhook_url"]:
        issues["unexpected_webhook"] = current["webhook_url"]
    for field in ("about", "description"):
        links = _foreign_links(current[field], EXPECTED_USERNAME)
        if links:
            issues[f"foreign_links_in_{field}"] = links
    for language, fields in current.get("localized", {}).items():
        for field in ("about", "description"):
            links = _foreign_links(fields[field], EXPECTED_USERNAME)
            if links:
                issues[f"foreign_links_in_{field}_{language}"] = links
    if baseline is None:
        issues["baseline"] = "Не утверждён эталон About, Description и аватара."
    else:
        for field in ("bot_id", "username", "name", "about", "description", "avatar_file_unique_id",
                      "commands", "localized"):
            if current[field] != baseline.get(field):
                issues[f"changed_{field}"] = {"expected": baseline.get(field), "actual": current[field]}
    return issues


def self_test(*, baseline_path: Path = BASELINE_FILE, log_file: Path = LOG_FILE,
              notify: bool = True) -> dict:
    """Exercise detection and local alerts without calling or changing Telegram."""
    baseline = load_baseline(baseline_path)
    if not baseline:
        raise RuntimeError(f"Сначала создайте эталон: {baseline_path}")
    simulated = dict(baseline)
    simulated["name"] = "ПРОВЕРКА ТРЕВОГИ — НЕ НАСТОЯЩЕЕ ИМЯ"
    simulated["about"] = "Тестовая подмена: https://t.me/NotOurBot"
    issues = differences(simulated, baseline)
    if "changed_name" not in issues or "foreign_links_in_about" not in issues:
        raise RuntimeError("Самопроверка не обнаружила смоделированную подмену.")
    write_event("SELF_TEST_ALERT", issues, log_file=log_file)
    if notify:
        alert_local("САМОПРОВЕРКА: имитация подмены профиля")
    return issues


async def check_once(bot, *, baseline_path: Path = BASELINE_FILE,
                     log_file: Path = LOG_FILE, notify: bool = True) -> dict:
    try:
        current = await snapshot(bot)
        issues = differences(current, load_baseline(baseline_path))
    except Exception as exc:
        issues = {"check_failed": _safe_error(exc, getattr(bot, "token", ""))}
    if issues:
        write_event("ALERT", issues, log_file=log_file)
        if notify:
            alert_local(", ".join(issues))
    else:
        write_event("OK", log_file=log_file)
    return issues


async def watch(bot, *, interval: int = CHECK_INTERVAL_SECONDS) -> None:
    """Check at startup, then periodically; alert once per distinct state."""
    previous: str | None = None
    while True:
        issues = await check_once(bot, notify=False)
        fingerprint = json.dumps(issues, ensure_ascii=False, sort_keys=True)
        if issues and fingerprint != previous:
            alert_local(", ".join(issues))
        elif not issues and previous is None:
            print("✅ OSBB security: публичный профиль и вебхук проверены; отклонений нет.", flush=True)
        elif not issues and previous and previous != "{}":
            write_event("RECOVERED")
            print("OSBB bot security profile restored.", flush=True)
        previous = fingerprint
        await asyncio.sleep(interval)


async def _cli(args: argparse.Namespace) -> int:
    if str(paths.SECRETS_DIR) not in sys.path:
        sys.path.insert(0, str(paths.SECRETS_DIR))
    from telegram_osbb import TOKEN  # Imported only after the Secret volume is available.
    from telegram import Bot

    async with Bot(TOKEN) as bot:
        if args.action == "show":
            print(json.dumps(await snapshot(bot), ensure_ascii=False, indent=2))
            return 0
        if args.action == "enroll":
            current = await snapshot(bot)
            print(json.dumps(current, ensure_ascii=False, indent=2))
            issues = differences(current, current)
            if issues:
                print("Нельзя принять профиль с отклонениями:", json.dumps(issues, ensure_ascii=False))
                return 2
            if input("Профиль проверен в BotFather? Для сохранения эталона введите ПОДТВЕРЖДАЮ: ") != "ПОДТВЕРЖДАЮ":
                print("Эталон не изменён.")
                return 1
            BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
            BASELINE_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write_event("BASELINE_ENROLLED", {"bot_id": current["bot_id"]})
            print(f"Эталон сохранён: {BASELINE_FILE}")
            return 0
        if args.action == "check":
            issues = await check_once(bot)
            if issues:
                print(json.dumps(issues, ensure_ascii=False, indent=2))
                return 2
            print("✅ Профиль и режим доставки бота соответствуют эталону.")
            return 0
        await watch(bot, interval=args.interval)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка безопасности Telegram-бота OSBB")
    parser.add_argument("action", choices=("show", "enroll", "check", "watch", "self-test", "prune"))
    parser.add_argument("--interval", type=int, default=CHECK_INTERVAL_SECONDS,
                        help="Интервал проверок в секундах для watch (по умолчанию 300)")
    args = parser.parse_args()
    if args.interval < 30:
        parser.error("Интервал должен быть не меньше 30 секунд.")
    try:
        if args.action == "prune":
            prune_log()
            print("Журнал безопасности очищен: сохранены две последние штатные проверки за сегодня и инциденты.")
            return 0
        if args.action == "self-test":
            self_test()
            print("✅ Самопроверка обнаружила подмену и записала SELF_TEST_ALERT; Telegram не менялся.")
            return 0
        return asyncio.run(_cli(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Проверка не запущена: {_safe_error(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
