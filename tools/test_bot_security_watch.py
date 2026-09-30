"""Offline checks for the read-only OSBB bot security watcher."""

import unittest

from bot_security_watch import differences, _foreign_links


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


if __name__ == "__main__":
    unittest.main()
