"""Offline checks for the read-only OSBB bot security watcher."""

import unittest
import json
import tempfile
from pathlib import Path

from bot_security_watch import differences, prune_log, recent_events, self_test, write_event, _foreign_links


PROFILE = {
    "bot_id": 8924565536,
    "username": "Parking_24a_GS_bot",
    "name": "Parking 24A GS",
    "about": "Офіційний бот ОСББ 24А.",
    "description": "Паркування і послуги для мешканців.",
    "avatar_file_unique_id": None,
    "commands": [],
    "localized": {"uk": {"name": "", "about": "", "description": ""}},
    "webhook_url": "",
}


class BotSecurityWatchTests(unittest.TestCase):
    def test_matching_profile(self):
        self.assertEqual(differences(PROFILE, PROFILE), {})

    def test_foreign_link_and_name_change(self):
        changed = dict(PROFILE, name="БРАТ И СЕСТРА", about="👉 https://t.me/OtherBot?start=braat")
        issues = differences(changed, PROFILE)
        self.assertIn("changed_name", issues)
        self.assertIn("foreign_links_in_about", issues)
        self.assertIn("changed_about", issues)

    def test_own_link_is_allowed(self):
        self.assertEqual(_foreign_links("https://t.me/Parking_24a_GS_bot", PROFILE["username"]), [])

    def test_unexpected_webhook_and_missing_baseline(self):
        changed = dict(PROFILE, webhook_url="https://example.net/hook")
        issues = differences(changed, None)
        self.assertIn("unexpected_webhook", issues)
        self.assertIn("baseline", issues)

    def test_localized_phishing_text(self):
        changed = dict(PROFILE, localized={"uk": {
            "name": "", "about": "https://t.me/OtherBot", "description": "",
        }})
        issues = differences(changed, PROFILE)
        self.assertIn("foreign_links_in_about_uk", issues)
        self.assertIn("changed_localized", issues)

    def test_self_test_records_labeled_alert_without_telegram(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline_path = root / "baseline.json"
            log_path = root / "security.log"
            baseline_path.write_text(json.dumps(PROFILE), encoding="utf-8")
            issues = self_test(baseline_path=baseline_path, log_file=log_path, notify=False)
            self.assertIn("foreign_links_in_about", issues)
            event = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(event["kind"], "SELF_TEST_ALERT")
            self.assertEqual(json.loads(baseline_path.read_text(encoding="utf-8")), PROFILE)

    def test_recent_events_skip_broken_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            log_path = Path(folder) / "security.log"
            write_event("OK", log_file=log_path)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("not json\n")
            self.assertEqual([event["kind"] for event in recent_events(log_file=log_path)], ["OK"])

    def test_normal_checks_keep_only_two_latest(self):
        with tempfile.TemporaryDirectory() as folder:
            log_path = Path(folder) / "security.log"
            write_event("ALERT", {"changed_name": "unexpected"}, log_file=log_path)
            for index in range(12):
                write_event("OK", {"run": index}, log_file=log_path)
            events = recent_events(log_file=log_path)
            self.assertEqual([event["details"]["run"] for event in events if event["kind"] == "OK"], [10, 11])
            self.assertEqual(sum(event["kind"] == "ALERT" for event in events), 1)

    def test_prune_removes_old_routine_entries_without_adding_an_event(self):
        with tempfile.TemporaryDirectory() as folder:
            log_path = Path(folder) / "security.log"
            old = [
                {"at": "2026-09-30T10:00:00+00:00", "kind": "OK", "details": {}},
                {"at": "2026-09-30T10:05:00+00:00", "kind": "SELF_TEST_ALERT", "details": {}},
                {"at": "2026-09-30T10:10:00+00:00", "kind": "ALERT", "details": {"reason": "incident"}},
            ]
            log_path.write_text("".join(json.dumps(item) + "\n" for item in old), encoding="utf-8")
            write_event("OK", {"run": 1}, log_file=log_path)
            write_event("OK", {"run": 2}, log_file=log_path)
            write_event("OK", {"run": 3}, log_file=log_path)
            prune_log(log_file=log_path)
            events = recent_events(log_file=log_path)
            self.assertEqual([item["kind"] for item in events], ["ALERT", "OK", "OK"])
            self.assertEqual([item["details"]["run"] for item in events if item["kind"] == "OK"], [2, 3])

    def test_incident_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            log_path = Path(folder) / "security.log"
            for index in range(60):
                write_event("ALERT", {"run": index}, log_file=log_path)
            events = recent_events(log_file=log_path)
            self.assertEqual(len(events), 50)
            self.assertEqual(events[0]["details"]["run"], 10)


if __name__ == "__main__":
    unittest.main()
