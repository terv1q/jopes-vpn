from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit


class PreferencesError(Exception):
    pass


DOCUMENTS_VERSION = "1"


class PreferencesStore:
    """Small persistent store for user-facing bot preferences.

    Only non-secret settings and notification bookkeeping live here. Panel
    remains the source of truth for accounts, subscriptions, and devices.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_preferences (
                        telegram_id INTEGER PRIMARY KEY,
                        reminders_enabled INTEGER NOT NULL DEFAULT 1,
                        language TEXT NOT NULL DEFAULT 'ru',
                        last_reminder_on TEXT,
                        last_reminder_days INTEGER,
                        first_name TEXT,
                        last_name TEXT,
                        username TEXT,
                        timezone_offset INTEGER NOT NULL DEFAULT 3,
                        first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                        onboarding_due_at TEXT,
                        onboarding_active INTEGER NOT NULL DEFAULT 0,
                        setup_tutorial_step INTEGER NOT NULL DEFAULT 3,
                        setup_tutorial_cursor INTEGER NOT NULL DEFAULT 0,
                        trial_used INTEGER NOT NULL DEFAULT 0,
                        trial_nudge_sent_at TEXT
                    )
                    """
                )
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(user_preferences)"
                    ).fetchall()
                }
                if "language" not in columns:
                    connection.execute(
                        "ALTER TABLE user_preferences "
                        "ADD COLUMN language TEXT NOT NULL DEFAULT 'ru'"
                    )
                if "first_name" not in columns:
                    connection.execute(
                        "ALTER TABLE user_preferences ADD COLUMN first_name TEXT"
                    )
                if "last_name" not in columns:
                    connection.execute(
                        "ALTER TABLE user_preferences ADD COLUMN last_name TEXT"
                    )
                if "username" not in columns:
                    connection.execute(
                        "ALTER TABLE user_preferences ADD COLUMN username TEXT"
                    )
                if "timezone_offset" not in columns:
                    connection.execute(
                        "ALTER TABLE user_preferences "
                        "ADD COLUMN timezone_offset INTEGER NOT NULL DEFAULT 3"
                    )
                for column, definition in (
                    ("onboarding_due_at", "TEXT"),
                    ("onboarding_active", "INTEGER NOT NULL DEFAULT 0"),
                    ("setup_tutorial_step", "INTEGER NOT NULL DEFAULT 3"),
                    ("setup_tutorial_cursor", "INTEGER NOT NULL DEFAULT 0"),
                    ("trial_used", "INTEGER NOT NULL DEFAULT 0"),
                    ("trial_nudge_sent_at", "TEXT"),
                    ("accepted_documents_version", "TEXT"),
                    ("documents_accepted_at", "TEXT"),
                ):
                    if column not in columns:
                        connection.execute(
                            f"ALTER TABLE user_preferences ADD COLUMN {column} {definition}"
                        )
                if "setup_tutorial_cursor" not in columns:
                    connection.execute(
                        """
                        UPDATE user_preferences
                        SET setup_tutorial_cursor = setup_tutorial_step
                        """
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS banner_file_ids (
                        banner TEXT PRIMARY KEY,
                        file_id TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bot_settings (
                        name TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS admin_plan_operations (
                        operation_key TEXT PRIMARY KEY,
                        admin_telegram_id INTEGER NOT NULL,
                        plan_code TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_navigation (
                        telegram_id INTEGER PRIMARY KEY,
                        stack_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS connection_log_state (
                        panel_user_id INTEGER PRIMARY KEY,
                        telegram_id INTEGER,
                        online_at TEXT,
                        last_logged_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS referrals (
                        referred_id INTEGER PRIMARY KEY,
                        referrer_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        activated_at TEXT,
                        reward_month TEXT,
                        reward_days INTEGER NOT NULL DEFAULT 0,
                        reward_status TEXT NOT NULL DEFAULT 'eligible'
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS campaign_button_clicks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        campaign TEXT NOT NULL,
                        protocol TEXT NOT NULL,
                        telegram_id INTEGER NOT NULL,
                        first_name TEXT NOT NULL,
                        last_name TEXT,
                        username TEXT,
                        clicked_at TEXT NOT NULL DEFAULT (datetime('now')),
                        completed_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS announcement_posts (
                        campaign TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        owner_id INTEGER NOT NULL,
                        preview_message_id INTEGER,
                        photo_file_id TEXT,
                        channel_message_id INTEGER,
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                announcement_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(announcement_posts)"
                    ).fetchall()
                }
                if "preview_message_id" not in announcement_columns:
                    connection.execute(
                        "ALTER TABLE announcement_posts "
                        "ADD COLUMN preview_message_id INTEGER"
                    )
                if "photo_file_id" not in announcement_columns:
                    connection.execute(
                        "ALTER TABLE announcement_posts ADD COLUMN photo_file_id TEXT"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS connection_log_endpoints (
                        panel_user_id INTEGER NOT NULL,
                        telegram_id INTEGER,
                        hwid TEXT NOT NULL,
                        ip TEXT NOT NULL DEFAULT '',
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        PRIMARY KEY (panel_user_id, hwid, ip)
                    )
                    """
                )
                endpoint_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(connection_log_endpoints)"
                    ).fetchall()
                }
                if "notified_at" not in endpoint_columns:
                    connection.execute(
                        "ALTER TABLE connection_log_endpoints "
                        "ADD COLUMN notified_at TEXT"
                    )
                    connection.execute(
                        """
                        UPDATE connection_log_endpoints
                        SET notified_at = first_seen_at
                        WHERE notified_at IS NULL
                        """
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS free_daily_state (
                        telegram_id INTEGER PRIMARY KEY,
                        day TEXT NOT NULL,
                        lifetime_baseline INTEGER
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS free_daily_reports (
                        telegram_id INTEGER NOT NULL,
                        report_day TEXT NOT NULL,
                        used_bytes INTEGER,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        sent_at TEXT,
                        PRIMARY KEY (telegram_id, report_day)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS traffic_notification_state (
                        telegram_id INTEGER NOT NULL,
                        panel_user_id INTEGER NOT NULL,
                        cycle_key TEXT NOT NULL,
                        threshold INTEGER NOT NULL,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        sent_at TEXT,
                        PRIMARY KEY (
                            telegram_id, panel_user_id, cycle_key, threshold
                        )
                    )
                    """
                )
                connection.commit()
        except (OSError, sqlite3.Error) as error:
            raise PreferencesError("Cannot open preferences database") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def remember(
        self,
        telegram_id: int,
        first_name: str | None = None,
        last_name: str | None = None,
        username: str | None = None,
    ) -> None:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        first = (first_name or "").strip() or None
        last = (last_name or "").strip() or None
        user_name = (username or "").strip().lstrip("@") or None
        if user_name is not None and len(user_name) > 32:
            user_name = None
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO user_preferences
                        (telegram_id, first_name, last_name, username)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        first_name = COALESCE(excluded.first_name, first_name),
                        last_name = COALESCE(excluded.last_name, last_name),
                        username = excluded.username
                    """,
                    (telegram_id, first, last, user_name),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot store user preference") from error

    def reset_navigation(self, telegram_id: int, screen: str = "home") -> None:
        self._save_navigation(telegram_id, [screen])

    def push_navigation(self, telegram_id: int, screen: str) -> None:
        if telegram_id <= 0 or not screen or len(screen) > 64:
            raise ValueError("Invalid navigation target")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT stack_json FROM user_navigation WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
                stack = self._decode_navigation(row["stack_json"]) if row else ["home"]
                if screen == "home":
                    stack = ["home"]
                elif stack[-1] != screen:
                    stack.append(screen)
                    stack = stack[-20:]
                connection.execute(
                    """
                    INSERT INTO user_navigation (telegram_id, stack_json)
                    VALUES (?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        stack_json = excluded.stack_json
                    """,
                    (telegram_id, json.dumps(stack)),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot update navigation history") from error

    def pop_navigation(self, telegram_id: int) -> str:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT stack_json FROM user_navigation WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
                stack = self._decode_navigation(row["stack_json"]) if row else ["home"]
                if len(stack) > 1:
                    stack.pop()
                connection.execute(
                    """
                    INSERT INTO user_navigation (telegram_id, stack_json)
                    VALUES (?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        stack_json = excluded.stack_json
                    """,
                    (telegram_id, json.dumps(stack)),
                )
                connection.commit()
                return stack[-1]
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read navigation history") from error

    def current_navigation(self, telegram_id: int) -> str:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT stack_json FROM user_navigation WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read navigation history") from error
        stack = self._decode_navigation(row["stack_json"]) if row else ["home"]
        return stack[-1]

    @staticmethod
    def _decode_navigation(value: str) -> list[str]:
        try:
            stack = json.loads(value)
        except ValueError as error:
            raise PreferencesError("Navigation history is malformed") from error
        if (
            not isinstance(stack, list)
            or not stack
            or any(not isinstance(screen, str) or not screen for screen in stack)
        ):
            raise PreferencesError("Navigation history is malformed")
        return stack[-20:]

    def _save_navigation(self, telegram_id: int, stack: list[str]) -> None:
        if telegram_id <= 0 or not stack:
            raise ValueError("Invalid navigation history")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO user_navigation (telegram_id, stack_json)
                    VALUES (?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        stack_json = excluded.stack_json
                    """,
                    (telegram_id, json.dumps(stack[-20:])),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot save navigation history") from error

    def register_start(
        self, telegram_id: int, referrer_id: int | None = None
    ) -> bool:
        if telegram_id <= 0 or (referrer_id is not None and referrer_id <= 0):
            raise ValueError("Telegram IDs must be positive")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO user_preferences
                        (
                            telegram_id, onboarding_due_at, first_seen_at,
                            setup_tutorial_step
                        )
                    VALUES (?, datetime('now', '+24 hours'), datetime('now'), 0)
                    ON CONFLICT(telegram_id) DO NOTHING
                    """,
                    (telegram_id,),
                )
                added_referral = False
                if referrer_id is not None and referrer_id != telegram_id:
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO referrals (referred_id, referrer_id)
                        VALUES (?, ?)
                        """,
                        (telegram_id, referrer_id),
                    )
                    added_referral = cursor.rowcount == 1
                connection.commit()
                return added_referral
        except sqlite3.Error as error:
            raise PreferencesError("Cannot register bot start") from error

    def mark_onboarding_activity(self, telegram_id: int) -> None:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE user_preferences
                    SET onboarding_active = 1
                    WHERE telegram_id = ? AND onboarding_due_at IS NOT NULL
                        AND trial_nudge_sent_at IS NULL
                    """,
                    (telegram_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot update onboarding activity") from error

    def pending_trial_nudges(self, limit: int = 25) -> list[int]:
        if limit < 1:
            raise ValueError("Limit must be positive")
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT telegram_id FROM user_preferences
                    WHERE onboarding_due_at <= datetime('now')
                        AND onboarding_active = 0
                        AND trial_used = 0
                        AND trial_nudge_sent_at IS NULL
                    ORDER BY onboarding_due_at
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read pending trial nudges") from error
        return [int(row["telegram_id"]) for row in rows]

    def mark_trial_nudge_sent(self, telegram_id: int) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE user_preferences SET trial_nudge_sent_at = datetime('now')
                    WHERE telegram_id = ?
                    """,
                    (telegram_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot mark trial nudge as sent") from error

    def mark_trial_used(self, telegram_id: int) -> None:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO user_preferences (telegram_id, trial_used)
                    VALUES (?, 1)
                    ON CONFLICT(telegram_id) DO UPDATE SET trial_used = 1
                    """,
                    (telegram_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot mark trial as used") from error

    def setup_tutorial_step(self, telegram_id: int) -> int:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT setup_tutorial_step FROM user_preferences "
                    "WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read setup tutorial progress") from error
        return 0 if row is None else int(row["setup_tutorial_step"])

    def setup_tutorial_cursor(self, telegram_id: int) -> int:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT setup_tutorial_step, setup_tutorial_cursor
                    FROM user_preferences WHERE telegram_id = ?
                    """,
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read setup tutorial position") from error
        if row is None:
            return 3
        progress = int(row["setup_tutorial_step"])
        cursor = int(row["setup_tutorial_cursor"])
        return 3 if progress >= 3 else min(max(cursor, 0), progress)

    def set_setup_tutorial_cursor(self, telegram_id: int, step: int) -> None:
        if telegram_id <= 0 or not 0 <= step <= 3:
            raise ValueError("Invalid setup tutorial position")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE user_preferences
                    SET setup_tutorial_cursor = MIN(?, setup_tutorial_step)
                    WHERE telegram_id = ?
                    """,
                    (step, telegram_id),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot save setup tutorial position") from error

    def set_setup_tutorial_step(self, telegram_id: int, step: int) -> None:
        if telegram_id <= 0 or not 0 <= step <= 3:
            raise ValueError("Invalid setup tutorial progress")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO user_preferences (telegram_id, setup_tutorial_step)
                    VALUES (?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        setup_tutorial_step = MAX(
                            user_preferences.setup_tutorial_step,
                            excluded.setup_tutorial_step
                        )
                    """,
                    (telegram_id, step),
                )
                connection.execute(
                    """
                    UPDATE user_preferences
                    SET setup_tutorial_cursor = MAX(setup_tutorial_cursor, ?)
                    WHERE telegram_id = ?
                    """,
                    (step, telegram_id),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot save setup tutorial progress") from error

    def trial_was_used(self, telegram_id: int) -> bool:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT trial_used FROM user_preferences WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read trial usage") from error
        return row is not None and bool(row["trial_used"])

    def referral_for(self, referred_id: int) -> int | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT referrer_id FROM referrals WHERE referred_id = ?",
                    (referred_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read referral") from error
        return None if row is None else int(row["referrer_id"])

    def log_campaign_click(
        self,
        campaign: str,
        protocol: str,
        telegram_id: int,
        first_name: str,
        last_name: str | None,
        username: str | None,
    ) -> int:
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO campaign_button_clicks (
                        campaign, protocol, telegram_id, first_name, last_name,
                        username
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        campaign,
                        protocol,
                        telegram_id,
                        first_name,
                        last_name,
                        username,
                    ),
                )
                connection.commit()
                return int(cursor.lastrowid)
        except sqlite3.Error as error:
            raise PreferencesError("Cannot log campaign button click") from error

    def pending_campaign_click(self, telegram_id: int) -> tuple[int, str] | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT id, protocol FROM campaign_button_clicks
                    WHERE telegram_id = ? AND completed_at IS NULL
                    ORDER BY id DESC LIMIT 1
                    """,
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read pending campaign click") from error
        return None if row is None else (int(row["id"]), str(row["protocol"]))

    def finish_campaign_click(self, click_id: int) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE campaign_button_clicks SET completed_at = datetime('now')
                    WHERE id = ? AND completed_at IS NULL
                    """,
                    (click_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot finish campaign button click") from error

    def begin_announcement_preview(self, campaign: str, owner_id: int) -> bool:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO announcement_posts (campaign, status, owner_id)
                    VALUES (?, 'preview_pending', ?)
                    """,
                    (campaign, owner_id),
                )
                cursor = connection.execute(
                    """
                    UPDATE announcement_posts
                    SET status = 'preview_sending', owner_id = ?,
                        updated_at = datetime('now')
                    WHERE campaign = ? AND status = 'preview_pending'
                    """,
                    (owner_id, campaign),
                )
                connection.commit()
                return cursor.rowcount == 1
        except sqlite3.Error as error:
            raise PreferencesError("Cannot start announcement preview") from error

    def finish_announcement_preview(
        self, campaign: str, preview_message_id: int
    ) -> None:
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    UPDATE announcement_posts SET status = 'awaiting_approval',
                        preview_message_id = ?, updated_at = datetime('now')
                    WHERE campaign = ? AND status = 'preview_sending'
                    """,
                    (preview_message_id, campaign),
                )
                connection.commit()
                if cursor.rowcount != 1:
                    raise PreferencesError("Announcement preview state changed")
        except sqlite3.Error as error:
            raise PreferencesError("Cannot finish announcement preview") from error

    def retry_announcement_preview(self, campaign: str) -> None:
        self._set_announcement_status(campaign, "preview_sending", "preview_pending")

    def recover_announcement_image_state(self, campaign: str) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE announcement_posts SET status = 'awaiting_approval',
                        updated_at = datetime('now')
                    WHERE campaign = ? AND status = 'awaiting_image'
                    """,
                    (campaign,),
                )
                connection.execute(
                    """
                    UPDATE announcement_posts SET status = 'awaiting_approval',
                        photo_file_id = NULL, updated_at = datetime('now')
                    WHERE campaign = ? AND status = 'preview_image_sending'
                    """,
                    (campaign,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot recover announcement image state") from error

    def begin_announcement_image(self, campaign: str, owner_id: int) -> bool:
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    UPDATE announcement_posts SET status = 'awaiting_image',
                        updated_at = datetime('now')
                    WHERE campaign = ? AND owner_id = ?
                        AND status = 'awaiting_approval'
                    """,
                    (campaign, owner_id),
                )
                connection.commit()
                return cursor.rowcount == 1
        except sqlite3.Error as error:
            raise PreferencesError("Cannot request announcement image") from error

    def cancel_announcement_image(self, campaign: str, owner_id: int) -> bool:
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    UPDATE announcement_posts SET status = 'awaiting_approval',
                        updated_at = datetime('now')
                    WHERE campaign = ? AND owner_id = ? AND status = 'awaiting_image'
                    """,
                    (campaign, owner_id),
                )
                connection.commit()
                return cursor.rowcount == 1
        except sqlite3.Error as error:
            raise PreferencesError("Cannot cancel announcement image request") from error

    def claim_announcement_image(
        self, campaign: str, owner_id: int, photo_file_id: str
    ) -> int | None:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE announcement_posts SET status = 'preview_image_sending',
                        photo_file_id = ?, updated_at = datetime('now')
                    WHERE campaign = ? AND owner_id = ? AND status = 'awaiting_image'
                    """,
                    (photo_file_id, campaign, owner_id),
                )
                row = connection.execute(
                    """
                    SELECT preview_message_id FROM announcement_posts
                    WHERE campaign = ? AND status = 'preview_image_sending'
                    """,
                    (campaign,),
                ).fetchone()
                if (
                    cursor.rowcount != 1
                    or row is None
                    or row["preview_message_id"] is None
                ):
                    connection.rollback()
                    return None
                connection.commit()
                return int(row["preview_message_id"])
        except sqlite3.Error as error:
            raise PreferencesError("Cannot save announcement image") from error

    def finish_announcement_image(
        self, campaign: str, preview_message_id: int
    ) -> None:
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    UPDATE announcement_posts SET status = 'awaiting_approval',
                        preview_message_id = ?, updated_at = datetime('now')
                    WHERE campaign = ? AND status = 'preview_image_sending'
                    """,
                    (preview_message_id, campaign),
                )
                connection.commit()
                if cursor.rowcount != 1:
                    raise PreferencesError("Announcement image state changed")
        except sqlite3.Error as error:
            raise PreferencesError("Cannot finish announcement image update") from error

    def retry_announcement_image(self, campaign: str) -> None:
        self._set_announcement_status(
            campaign, "preview_image_sending", "awaiting_image"
        )

    def announcement_photo(self, campaign: str) -> str | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT photo_file_id FROM announcement_posts WHERE campaign = ?",
                    (campaign,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read announcement image") from error
        return None if row is None else row["photo_file_id"]

    def claim_announcement_publication(
        self, campaign: str, owner_id: int
    ) -> bool:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE announcement_posts SET status = 'publishing',
                        updated_at = datetime('now')
                    WHERE campaign = ? AND owner_id = ? AND status = 'awaiting_approval'
                    """,
                    (campaign, owner_id),
                )
                connection.commit()
                return cursor.rowcount == 1
        except sqlite3.Error as error:
            raise PreferencesError("Cannot claim announcement publication") from error

    def finish_announcement_publication(
        self, campaign: str, channel_message_id: int
    ) -> None:
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    UPDATE announcement_posts SET status = 'published',
                        channel_message_id = ?, updated_at = datetime('now')
                    WHERE campaign = ? AND status = 'publishing'
                    """,
                    (channel_message_id, campaign),
                )
                connection.commit()
                if cursor.rowcount != 1:
                    raise PreferencesError("Announcement publication is not pending")
        except sqlite3.Error as error:
            raise PreferencesError("Cannot finish announcement publication") from error

    def retry_announcement_publication(self, campaign: str) -> None:
        self._set_announcement_status(campaign, "publishing", "awaiting_approval")

    def cancel_announcement(self, campaign: str, owner_id: int) -> bool:
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    UPDATE announcement_posts SET status = 'cancelled',
                        updated_at = datetime('now')
                    WHERE campaign = ? AND owner_id = ? AND status = 'awaiting_approval'
                    """,
                    (campaign, owner_id),
                )
                connection.commit()
                return cursor.rowcount == 1
        except sqlite3.Error as error:
            raise PreferencesError("Cannot cancel announcement") from error

    def announcement_status(self, campaign: str) -> str | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT status FROM announcement_posts WHERE campaign = ?",
                    (campaign,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read announcement status") from error
        return None if row is None else str(row["status"])

    def _set_announcement_status(
        self, campaign: str, previous: str, current: str
    ) -> None:
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    UPDATE announcement_posts SET status = ?,
                        updated_at = datetime('now')
                    WHERE campaign = ? AND status = ?
                    """,
                    (current, campaign, previous),
                )
                connection.commit()
                if cursor.rowcount != 1:
                    raise PreferencesError("Announcement state changed")
        except sqlite3.Error as error:
            raise PreferencesError("Cannot update announcement status") from error

    def reserve_referral_reward(
        self,
        referred_id: int,
        *,
        reward_month: str,
        reward_days: int,
        monthly_cap: int,
    ) -> bool:
        if reward_days < 1 or monthly_cap < reward_days:
            raise ValueError("Invalid referral reward limits")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                referral = connection.execute(
                    """
                    SELECT referrer_id, reward_status FROM referrals
                    WHERE referred_id = ?
                    """,
                    (referred_id,),
                ).fetchone()
                if referral is None or referral["reward_status"] != "eligible":
                    connection.rollback()
                    return False
                granted = connection.execute(
                    """
                    SELECT COALESCE(SUM(reward_days), 0) AS total
                    FROM referrals
                    WHERE referrer_id = ? AND reward_month = ?
                        AND reward_status IN ('pending', 'granted')
                    """,
                    (int(referral["referrer_id"]), reward_month),
                ).fetchone()
                if int(granted["total"]) + reward_days > monthly_cap:
                    connection.execute(
                        """
                        UPDATE referrals
                        SET reward_status = 'capped',
                            activated_at = datetime('now')
                        WHERE referred_id = ?
                        """,
                        (referred_id,),
                    )
                    connection.commit()
                    return False
                connection.execute(
                    """
                    UPDATE referrals
                    SET reward_status = 'pending',
                        reward_month = ?,
                        reward_days = ?,
                        activated_at = datetime('now')
                    WHERE referred_id = ?
                    """,
                    (reward_month, reward_days, referred_id),
                )
                connection.commit()
                return True
        except sqlite3.Error as error:
            raise PreferencesError("Cannot reserve referral reward") from error

    def mark_referral_activated(self, referred_id: int) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE referrals SET activated_at = datetime('now')
                    WHERE referred_id = ? AND activated_at IS NULL
                    """,
                    (referred_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot mark referral as activated") from error

    def finish_referral_reward(self, referred_id: int) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE referrals SET reward_status = 'granted'
                    WHERE referred_id = ? AND reward_status = 'pending'
                    """,
                    (referred_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot finish referral reward") from error

    def skip_referral_reward(self, referred_id: int) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE referrals SET reward_status = 'skipped',
                        reward_month = NULL, reward_days = 0,
                        activated_at = datetime('now')
                    WHERE referred_id = ?
                        AND reward_status IN ('eligible', 'pending')
                    """,
                    (referred_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot skip referral reward") from error

    def referral_stats(self, referrer_id: int) -> dict[str, int]:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS invited,
                        SUM(CASE WHEN activated_at IS NOT NULL THEN 1 ELSE 0 END)
                            AS activated,
                        COALESCE(SUM(CASE WHEN reward_status = 'granted'
                            THEN reward_days ELSE 0 END), 0) AS reward_days,
                        SUM(CASE WHEN reward_status = 'pending' THEN 1 ELSE 0 END)
                            AS pending
                    FROM referrals WHERE referrer_id = ?
                    """,
                    (referrer_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read referral statistics") from error
        return {
            "invited": int(row["invited"] or 0),
            "activated": int(row["activated"] or 0),
            "reward_days": int(row["reward_days"] or 0),
            "pending": int(row["pending"] or 0),
        }

    def display_name(self, telegram_id: int) -> str | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT first_name, last_name FROM user_preferences "
                    "WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read user preference") from error
        if row is None:
            return None
        name = " ".join(
            part for part in (row["first_name"], row["last_name"]) if part
        ).strip()
        return name or None

    def display_names(self) -> dict[int, str]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT telegram_id, first_name, last_name "
                    "FROM user_preferences"
                ).fetchall()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read user preferences") from error
        names: dict[int, str] = {}
        for row in rows:
            name = " ".join(
                part for part in (row["first_name"], row["last_name"]) if part
            ).strip()
            if name:
                names[int(row["telegram_id"])] = name
        return names

    def display_usernames(self) -> dict[int, str]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT telegram_id, username FROM user_preferences "
                    "WHERE username IS NOT NULL"
                ).fetchall()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read user preferences") from error
        return {
            int(row["telegram_id"]): str(row["username"])
            for row in rows
            if row["username"]
        }

    def reminders_enabled(self, telegram_id: int) -> bool:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT reminders_enabled FROM user_preferences WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read user preference") from error
        return True if row is None else bool(row["reminders_enabled"])

    def set_reminders(self, telegram_id: int, enabled: bool) -> None:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO user_preferences (telegram_id, reminders_enabled)
                    VALUES (?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE
                    SET reminders_enabled = excluded.reminders_enabled
                    """,
                    (telegram_id, int(enabled)),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot store user preference") from error

    def known_users(self) -> list[int]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT telegram_id FROM user_preferences ORDER BY telegram_id"
                ).fetchall()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read user preferences") from error
        return [int(row["telegram_id"]) for row in rows]

    def free_daily_state(
        self, telegram_id: int
    ) -> tuple[str, int | None] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT day, lifetime_baseline FROM free_daily_state "
                "WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        if row is None:
            return None
        baseline = row["lifetime_baseline"]
        return str(row["day"]), int(baseline) if baseline is not None else None

    def initialize_free_daily(
        self, telegram_id: int, day: str, lifetime_baseline: int | None
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO free_daily_state
                    (telegram_id, day, lifetime_baseline)
                VALUES (?, ?, ?)
                """,
                (telegram_id, day, lifetime_baseline),
            )
            connection.commit()

    def rollover_free_daily(
        self,
        telegram_id: int,
        previous_day: str,
        current_day: str,
        used_bytes: int | None,
        lifetime_baseline: int | None,
    ) -> bool:
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT day FROM free_daily_state WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
                if row is None or row["day"] != previous_day:
                    connection.rollback()
                    return False
                connection.execute(
                    """
                    INSERT OR IGNORE INTO free_daily_reports
                        (telegram_id, report_day, used_bytes)
                    VALUES (?, ?, ?)
                    """,
                    (telegram_id, previous_day, used_bytes),
                )
                connection.execute(
                    """
                    UPDATE free_daily_state
                    SET day = ?, lifetime_baseline = ?
                    WHERE telegram_id = ? AND day = ?
                    """,
                    (current_day, lifetime_baseline, telegram_id, previous_day),
                )
                connection.commit()
                return True
            except sqlite3.Error as error:
                connection.rollback()
                raise PreferencesError("Free usage state could not be updated") from error

    def pending_free_daily_reports(
        self, limit: int = 100
    ) -> list[tuple[int, str, int | None]]:
        if not 1 <= limit <= 1000:
            raise ValueError("Report limit is out of range")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT telegram_id, report_day, used_bytes
                FROM free_daily_reports
                WHERE sent_at IS NULL
                ORDER BY report_day, telegram_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            (
                int(row["telegram_id"]),
                str(row["report_day"]),
                int(row["used_bytes"]) if row["used_bytes"] is not None else None,
            )
            for row in rows
        ]

    def mark_free_daily_report_sent(
        self, telegram_id: int, report_day: str
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE free_daily_reports
                SET sent_at = datetime('now')
                WHERE telegram_id = ? AND report_day = ? AND sent_at IS NULL
                """,
                (telegram_id, report_day),
            )
            connection.commit()

    def claim_traffic_notification(
        self,
        telegram_id: int,
        panel_user_id: int,
        cycle_key: str,
        threshold: int,
    ) -> bool:
        if (
            telegram_id <= 0
            or panel_user_id <= 0
            or not cycle_key
            or threshold not in {50, 75, 100}
        ):
            raise ValueError("Invalid traffic notification")
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO traffic_notification_state
                        (telegram_id, panel_user_id, cycle_key, threshold)
                    VALUES (?, ?, ?, ?)
                    """,
                    (telegram_id, panel_user_id, cycle_key, threshold),
                )
                if cursor.rowcount == 1:
                    connection.commit()
                    return True
                cursor = connection.execute(
                    """
                    UPDATE traffic_notification_state
                    SET created_at = datetime('now')
                    WHERE telegram_id = ? AND panel_user_id = ?
                        AND cycle_key = ? AND threshold = ? AND sent_at IS NULL
                        AND created_at <= datetime('now', '-10 minutes')
                    """,
                    (telegram_id, panel_user_id, cycle_key, threshold),
                )
                connection.commit()
                return cursor.rowcount == 1
        except sqlite3.Error as error:
            raise PreferencesError(
                "Cannot reserve traffic notification"
            ) from error

    def mark_traffic_notification_sent(
        self,
        telegram_id: int,
        panel_user_id: int,
        cycle_key: str,
        threshold: int,
    ) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE traffic_notification_state
                    SET sent_at = datetime('now')
                    WHERE telegram_id = ? AND panel_user_id = ?
                        AND cycle_key = ? AND threshold = ? AND sent_at IS NULL
                    """,
                    (telegram_id, panel_user_id, cycle_key, threshold),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError(
                "Cannot record traffic notification delivery"
            ) from error

    def release_traffic_notification(
        self,
        telegram_id: int,
        panel_user_id: int,
        cycle_key: str,
        threshold: int,
    ) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    DELETE FROM traffic_notification_state
                    WHERE telegram_id = ? AND panel_user_id = ?
                        AND cycle_key = ? AND threshold = ? AND sent_at IS NULL
                    """,
                    (telegram_id, panel_user_id, cycle_key, threshold),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError(
                "Cannot release traffic notification"
            ) from error

    def banner_file_id(self, banner: str) -> str | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT file_id FROM banner_file_ids WHERE banner = ?",
                    (banner,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read banner cache") from error
        if row is None or not row["file_id"]:
            return None
        return str(row["file_id"])

    def set_banner_file_id(self, banner: str, file_id: str) -> None:
        if not banner or not file_id:
            raise ValueError("Banner name and file ID are required")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO banner_file_ids (banner, file_id)
                    VALUES (?, ?)
                    ON CONFLICT(banner) DO UPDATE SET
                        file_id = excluded.file_id,
                        updated_at = datetime('now')
                    """,
                    (banner, file_id),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot store banner cache") from error

    def setting(self, name: str, default: str | None = None) -> str | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT value FROM bot_settings WHERE name = ?",
                    (name,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read bot setting") from error
        if row is None:
            return default
        return str(row["value"])

    def set_setting(self, name: str, value: str) -> None:
        if not name:
            raise ValueError("Setting name is required")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO bot_settings (name, value)
                    VALUES (?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        value = excluded.value,
                        updated_at = datetime('now')
                    """,
                    (name, value),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot store bot setting") from error

    def documents_accepted(self, telegram_id: int, version: str) -> bool:
        if telegram_id <= 0 or not version:
            raise ValueError("Telegram ID and document version are required")
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT accepted_documents_version
                    FROM user_preferences WHERE telegram_id = ?
                    """,
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read document consent") from error
        return row is not None and row["accepted_documents_version"] == version

    def accept_documents(self, telegram_id: int, version: str) -> None:
        if telegram_id <= 0 or not version:
            raise ValueError("Telegram ID and document version are required")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO user_preferences (
                        telegram_id, accepted_documents_version, documents_accepted_at
                    ) VALUES (?, ?, datetime('now'))
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        accepted_documents_version = excluded.accepted_documents_version,
                        documents_accepted_at = excluded.documents_accepted_at
                    """,
                    (telegram_id, version),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot store document consent") from error

    def document_url(self, document: str) -> str | None:
        if document not in {"privacy", "agreement"}:
            raise ValueError("Unknown document")
        value = self.setting(f"document_url_{document}")
        if not value:
            return None
        try:
            parsed = urlsplit(value)
            parsed.port
            valid = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not any(char.isspace() for char in value)
            )
        except ValueError:
            valid = False
        if not valid:
            raise PreferencesError("Stored document URL is invalid")
        return value

    def documents_version(self) -> str:
        value = self.setting("documents_version", DOCUMENTS_VERSION)
        if value is None:
            return DOCUMENTS_VERSION
        if not value.isascii() or not value.isdigit() or len(value) > 8:
            raise PreferencesError("Document version is invalid")
        return value

    def set_document_url(self, document: str, url: str | None) -> None:
        if document not in {"privacy", "agreement"}:
            raise ValueError("Unknown document")
        if url is not None:
            try:
                parsed = urlsplit(url)
                parsed.port
                valid_url = (
                    len(url) <= 2048
                    and url.isascii()
                    and parsed.scheme == "https"
                    and bool(parsed.hostname)
                    and parsed.username is None
                    and parsed.password is None
                    and not any(char.isspace() for char in url)
                )
            except (TypeError, ValueError):
                valid_url = False
            if not valid_url:
                raise ValueError("Document URL must be a valid HTTPS URL")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT value FROM bot_settings WHERE name = 'documents_version'"
                ).fetchone()
                current_version = (
                    current["value"] if current is not None else DOCUMENTS_VERSION
                )
                try:
                    next_version = str(int(current_version) + 1)
                except ValueError as error:
                    raise PreferencesError(
                        "Document version is invalid"
                    ) from error
                connection.execute(
                    """
                    INSERT INTO bot_settings (name, value)
                    VALUES (?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        value = excluded.value,
                        updated_at = datetime('now')
                    """,
                    (f"document_url_{document}", url or ""),
                )
                connection.execute(
                    """
                    INSERT INTO bot_settings (name, value)
                    VALUES ('documents_version', ?)
                    ON CONFLICT(name) DO UPDATE SET
                        value = excluded.value,
                        updated_at = datetime('now')
                    """,
                    (next_version,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot update document link") from error

    def claim_admin_plan_operation(
        self, operation_key: str, admin_telegram_id: int, plan_code: str
    ) -> bool:
        if (
            not operation_key
            or len(operation_key) > 32
            or admin_telegram_id <= 0
            or not plan_code
            or len(plan_code) > 64
        ):
            raise ValueError("Administrator plan operation identity is invalid")
        try:
            with closing(self._connect()) as connection:
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO admin_plan_operations (
                        operation_key, admin_telegram_id, plan_code, status
                    ) VALUES (?, ?, ?, 'PENDING')
                    """,
                    (operation_key, admin_telegram_id, plan_code),
                )
                connection.commit()
                return inserted.rowcount == 1
        except sqlite3.Error as error:
            raise PreferencesError(
                "Cannot claim administrator plan operation"
            ) from error

    def complete_admin_plan_operation(
        self, operation_key: str, *, review_required: bool = False
    ) -> None:
        if not operation_key:
            raise ValueError("Operation key is required")
        status = "REVIEW" if review_required else "COMPLETED"
        try:
            with closing(self._connect()) as connection:
                changed = connection.execute(
                    """
                    UPDATE admin_plan_operations
                    SET status = ?, updated_at = datetime('now')
                    WHERE operation_key = ? AND status = 'PENDING'
                    """,
                    (status, operation_key),
                ).rowcount
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError(
                "Cannot complete administrator plan operation"
            ) from error
        if changed != 1:
            raise PreferencesError(
                "Administrator plan operation is missing or already completed"
            )

    def admin_logs_enabled(self) -> bool:
        return self.setting("admin_connection_logs", "0") == "1"

    def set_admin_logs_enabled(self, enabled: bool) -> None:
        self.set_setting("admin_connection_logs", "1" if enabled else "0")

    def connection_state(self, panel_user_id: int) -> dict[str, str | None] | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT telegram_id, online_at, last_logged_at
                    FROM connection_log_state WHERE panel_user_id = ?
                    """,
                    (panel_user_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read connection log state") from error
        if row is None:
            return None
        return {
            "telegram_id": str(row["telegram_id"])
            if row["telegram_id"] is not None
            else None,
            "online_at": row["online_at"],
            "last_logged_at": row["last_logged_at"],
        }

    def save_connection_state(
        self,
        panel_user_id: int,
        telegram_id: int,
        online_at: str | None,
        *,
        logged_at: str | None = None,
    ) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO connection_log_state
                        (panel_user_id, telegram_id, online_at, last_logged_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(panel_user_id) DO UPDATE SET
                        telegram_id = excluded.telegram_id,
                        online_at = excluded.online_at,
                        last_logged_at = COALESCE(
                            excluded.last_logged_at,
                            connection_log_state.last_logged_at
                        )
                    """,
                    (panel_user_id, telegram_id, online_at, logged_at),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot store connection log state") from error

    def register_endpoint(
        self,
        panel_user_id: int,
        telegram_id: int,
        hwid: str,
        ip: str,
        *,
        now: str,
    ) -> bool:
        """Observe an endpoint; return whether its notification is pending."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT notified_at FROM connection_log_endpoints
                    WHERE panel_user_id = ? AND hwid = ? AND ip = ?
                    """,
                    (panel_user_id, hwid, ip),
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO connection_log_endpoints
                            (panel_user_id, telegram_id, hwid, ip,
                             first_seen_at, last_seen_at, notified_at)
                        VALUES (?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (panel_user_id, telegram_id, hwid, ip, now, now),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE connection_log_endpoints
                        SET last_seen_at = ?, telegram_id = ?
                        WHERE panel_user_id = ? AND hwid = ? AND ip = ?
                        """,
                        (now, telegram_id, panel_user_id, hwid, ip),
                    )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot store connection endpoint") from error
        return row is None or row["notified_at"] is None

    def mark_endpoint_notified(
        self,
        panel_user_id: int,
        hwid: str,
        ip: str,
        *,
        notified_at: str,
    ) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE connection_log_endpoints
                    SET notified_at = ?
                    WHERE panel_user_id = ? AND hwid = ? AND ip = ?
                    """,
                    (notified_at, panel_user_id, hwid, ip),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot mark connection log as delivered") from error

    def accounts_on_ip(self, ip: str, *, since_hours: int = 24) -> list[int]:
        if not ip:
            return []
        threshold = (
            datetime.now(UTC) - timedelta(hours=max(since_hours, 1))
        ).isoformat()
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT DISTINCT telegram_id FROM connection_log_endpoints
                    WHERE ip = ? AND telegram_id IS NOT NULL
                        AND last_seen_at >= ?
                    ORDER BY telegram_id
                    """,
                    (ip, threshold),
                ).fetchall()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read connection endpoints") from error
        return [int(row["telegram_id"]) for row in rows]

    def language(self, telegram_id: int, default: str = "ru") -> str:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT language FROM user_preferences WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read user language") from error
        if row is None or not row["language"]:
            return default
        return str(row["language"])

    def set_language(self, telegram_id: int, language: str) -> None:
        if telegram_id <= 0:
            raise ValueError("Telegram ID must be positive")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO user_preferences (telegram_id, language)
                    VALUES (?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE
                    SET language = excluded.language
                    """,
                    (telegram_id, language),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot store user language") from error

    def timezone_offset(self, telegram_id: int, default: int = 3) -> int:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT timezone_offset FROM user_preferences "
                    "WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read user timezone") from error
        return default if row is None else int(row["timezone_offset"])

    def set_timezone_offset(self, telegram_id: int, offset: int) -> None:
        if telegram_id <= 0 or not -12 <= offset <= 14:
            raise ValueError("Invalid user timezone")
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO user_preferences (telegram_id, timezone_offset)
                    VALUES (?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE
                    SET timezone_offset = excluded.timezone_offset
                    """,
                    (telegram_id, offset),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot store user timezone") from error

    def reminder_was_sent(
        self, telegram_id: int, day: date, days_left: int
    ) -> bool:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT last_reminder_on, last_reminder_days
                    FROM user_preferences WHERE telegram_id = ?
                    """,
                    (telegram_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot read reminder state") from error
        if row is None or row["last_reminder_on"] is None:
            return False
        return (
            row["last_reminder_on"] == day.isoformat()
            and row["last_reminder_days"] == days_left
        )

    def mark_reminder_sent(self, telegram_id: int, day: date, days_left: int) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    UPDATE user_preferences
                    SET last_reminder_on = ?, last_reminder_days = ?
                    WHERE telegram_id = ?
                    """,
                    (day.isoformat(), days_left, telegram_id),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise PreferencesError("Cannot store reminder state") from error
