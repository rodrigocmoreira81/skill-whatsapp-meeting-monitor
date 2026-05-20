from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("morning_reminder.py")
SPEC = importlib.util.spec_from_file_location("morning_reminder", MODULE_PATH)
morning_reminder = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(morning_reminder)


NOW = datetime(2026, 5, 18, 18, 0, tzinfo=timezone.utc)


class ClassifyTest(unittest.TestCase):
    def test_terminal_status_recent_is_terminal_recent(self):
        bucket, item = morning_reminder.classify(
            NOW,
            {
                "status": "invite_created",
                "completed_at_utc": (NOW - timedelta(hours=2)).isoformat(),
                "contact": "Contato teste",
            },
        )

        self.assertEqual(bucket, "terminal_recent")
        self.assertEqual(item, {"contact": "Contato teste", "status": "invite_created"})

    def test_terminal_status_older_than_lookback_is_ignored(self):
        bucket, item = morning_reminder.classify(
            NOW,
            {
                "status": "invite_created",
                "completed_at_utc": (NOW - timedelta(hours=25)).isoformat(),
                "contact": "Contato teste",
            },
        )

        self.assertIsNone(bucket)
        self.assertIsNone(item)

    def test_terminal_status_without_completed_at_is_ignored(self):
        bucket, item = morning_reminder.classify(
            NOW,
            {"status": "cancelled", "contact": "Contato teste"},
        )

        self.assertIsNone(bucket)
        self.assertIsNone(item)

    def test_empty_or_missing_status_is_ignored(self):
        for status in (None, ""):
            req = {"contact": "Contato teste"}
            if status is not None:
                req["status"] = status

            bucket, item = morning_reminder.classify(NOW, req)

            self.assertIsNone(bucket)
            self.assertIsNone(item)

    def test_terminal_status_with_whitespace_and_case_is_normalized(self):
        bucket, item = morning_reminder.classify(
            NOW,
            {
                "status": " Invite_Created ",
                "completed_at_utc": (NOW - timedelta(hours=2)).isoformat(),
                "contact": "Contato teste",
            },
        )

        self.assertEqual(bucket, "terminal_recent")
        self.assertEqual(item, {"contact": "Contato teste", "status": "invite_created"})

    def test_active_status_is_monitoring(self):
        bucket, item = morning_reminder.classify(
            NOW,
            {
                "status": "monitoring",
                "contact": "Contato teste",
                "modality": "meet",
                "expires_at_utc": "2026-05-24T07:00:00+00:00",
            },
        )

        self.assertEqual(bucket, "monitoring")
        self.assertEqual(
            item,
            {
                "contact": "Contato teste",
                "modality": "meet",
                "expires_at_utc": "2026-05-24T07:00:00+00:00",
            },
        )


if __name__ == "__main__":
    unittest.main()
