"""Visible local security status and safe self-test for the OSBB bot."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot_security_watch import LOG_FILE, recent_events, self_test


def display_time(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(ZoneInfo("Europe/Dublin")).strftime("%d.%m.%Y %H:%M:%S")
    except (TypeError, ValueError):
        return value or "—"


st.set_page_config(page_title="Безопасность бота", page_icon="🛡️", layout="wide")
st.title("🛡️ Безопасность Telegram-бота")
st.caption("Здесь видны результаты проверки и тестовые тревоги. Токен и настройки Telegram не меняются.")

events = recent_events(limit=200)
real_checks = [e for e in events if e.get("kind") in {"OK", "ALERT", "CONFLICT", "UNAUTHORIZED"}]
last_check = real_checks[-1] if real_checks else None
last_alert = next((e for e in reversed(events) if e.get("kind") in {"ALERT", "CONFLICT", "UNAUTHORIZED"}), None)
last_test = next((e for e in reversed(events) if e.get("kind") == "SELF_TEST_ALERT"), None)

if last_check:
    when = display_time(last_check.get("at", ""))
    if last_check["kind"] == "OK":
        st.success(f"✅ Последняя проверка: отклонений нет · {when}")
    else:
        st.error(f"🚨 Последняя проверка: {last_check['kind']} · {when}")
        st.json(last_check.get("details") or {})
else:
    st.warning("Проверка ещё не записана. Нажмите «Проверить сейчас».")

if last_alert:
    st.caption(f"Последняя реальная тревога: {display_time(last_alert.get('at', ''))} · {last_alert['kind']}.")
if last_test:
    st.info(f"Последняя тестовая тревога: {display_time(last_test.get('at', ''))}. Это не изменение Telegram.")

check_col, test_col = st.columns(2)
with check_col:
    if st.button("🔎 Проверить сейчас", type="primary", use_container_width=True):
        try:
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "bot_security_watch.py"), "check"],
                cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=35,
            )
            if result.returncode == 0:
                st.success("Текущий профиль и вебхук соответствуют эталону.")
            else:
                st.error("Проверка обнаружила отклонение или не смогла связаться с Telegram.")
                st.code((result.stdout + result.stderr)[-3000:])
        except subprocess.TimeoutExpired:
            st.error("Telegram не ответил за 35 секунд. Проверьте соединение и повторите.")
with test_col:
    test_macos = st.checkbox("Попробовать также уведомление macOS", value=False)
    if st.button("🧪 Испытать тревогу (без изменений в Telegram)", use_container_width=True):
        issues = self_test(notify=test_macos)
        st.error("🧪 ТЕСТОВАЯ ТРЕВОГА: обнаружены подменённое имя и чужая ссылка. В Telegram ничего не менялось.")
        st.caption(f"Обнаружено признаков: {len(issues)}. В журнале запись помечена SELF_TEST_ALERT.")

st.markdown("#### Последние события")
if events:
    for event in reversed(events[-20:]):
        kind = event.get("kind", "?")
        marker = "🧪" if kind == "SELF_TEST_ALERT" else "🚨" if kind in {"ALERT", "CONFLICT", "UNAUTHORIZED"} else "✅"
        with st.expander(f"{marker} {display_time(event.get('at', ''))} · {kind}"):
            st.json(event.get("details") or {})
else:
    st.info("Событий пока нет.")
st.caption(f"Локальный журнал: {LOG_FILE}. Не публикуйте его целиком: там могут быть ссылки из подменённого профиля.")
