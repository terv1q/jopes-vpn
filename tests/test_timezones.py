from __future__ import annotations

import unittest
from datetime import UTC, datetime

from jopesvpn.timezones import (
    ADMIN_UTC_OFFSET,
    format_datetime,
    set_utc_offset,
    utc_label,
)


class TimezoneFormattingTests(unittest.TestCase):
    def test_formats_utc_timestamp_in_selected_offset(self) -> None:
        self.assertEqual(
            format_datetime("2026-10-17T12:30:00Z", 3),
            "17.10.2026 15:30 [UTC+3]",
        )

    def test_admin_offset_is_utc_plus_five(self) -> None:
        self.assertEqual(ADMIN_UTC_OFFSET, 5)
        self.assertEqual(
            format_datetime(datetime(2026, 10, 17, 12, tzinfo=UTC), ADMIN_UTC_OFFSET),
            "17.10.2026 17:00 [UTC+5]",
        )

    def test_formats_naive_database_timestamp_as_utc(self) -> None:
        self.assertEqual(
            format_datetime("2026-10-17 12:30:00", -4),
            "17.10.2026 08:30 [UTC-4]",
        )

    def test_invalid_or_missing_timestamp_is_not_rendered_raw(self) -> None:
        self.assertEqual(format_datetime("not a timestamp", 3), "—")
        self.assertEqual(format_datetime(None, 3), "—")

    def test_timezone_offsets_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            set_utc_offset(15)
        self.assertEqual(utc_label(-12), "UTC-12")


if __name__ == "__main__":
    unittest.main()
