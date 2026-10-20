from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    panel_api_url: str
    panel_api_token: str
    admin_telegram_ids: frozenset[int]
    panel_internal_squad_uuid: str | None = None
    panel_ca_bundle: str | None = None
    plans_file: str | None = None
    support_contact: str | None = None
    trial_channel_username: str = "jopesvpn"
    trial_days: int = 3
    traffic_plans_enabled: bool = False
    free_access_enabled: bool = False
    subscription_page_url: str | None = None
    ticket_portal_url: str | None = None
    state_db_path: str | None = None
    owner_telegram_id: int | None = None

    @property
    def trial_channel_url(self) -> str:
        return f"https://t.me/{self.trial_channel_username}"

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = None,
        *,
        require_panel: bool = True,
    ) -> Settings:
        if env_file is not None:
            load_dotenv(dotenv_path=env_file, override=False)
        else:
            load_dotenv(override=False)

        bot_token = os.environ.get("BOT_TOKEN", "").strip()
        panel_api_url = (
            os.environ.get("PANEL_API_URL", "").strip().rstrip("/")
            if require_panel
            else ""
        )
        panel_api_token = (
            os.environ.get("PANEL_API_TOKEN", "").strip() if require_panel else ""
        )
        if not bot_token:
            raise ConfigurationError("BOT_TOKEN is required")
        if bot_token.startswith("REPLACE_WITH_"):
            raise ConfigurationError("BOT_TOKEN must not be a placeholder")
        if panel_api_token.startswith("REPLACE_WITH_"):
            panel_api_token = ""
        if require_panel and not panel_api_token:
            raise ConfigurationError("PANEL_API_TOKEN is required")
        if require_panel and not panel_api_url:
            raise ConfigurationError("PANEL_API_URL is required")

        if require_panel:
            try:
                parsed_panel_url = urlsplit(panel_api_url)
                parsed_panel_url.port
            except ValueError as error:
                raise ConfigurationError("PANEL_API_URL is invalid") from error
            if (
                parsed_panel_url.scheme != "https"
                or not parsed_panel_url.hostname
                or parsed_panel_url.hostname == "invalid"
                or parsed_panel_url.hostname.endswith(".invalid")
                or parsed_panel_url.username is not None
                or parsed_panel_url.password is not None
                or parsed_panel_url.query
                or parsed_panel_url.fragment
                or parsed_panel_url.path
            ):
                raise ConfigurationError("PANEL_API_URL must be a real HTTPS origin")

        admin_ids: set[int] = set()
        raw_admin_ids = os.environ.get("BOT_ADMIN_IDS", "")
        try:
            admin_ids = {
                int(value.strip())
                for value in raw_admin_ids.split(",")
                if value.strip()
            }
        except ValueError as error:
            raise ConfigurationError("BOT_ADMIN_IDS must be comma-separated integers") from error
        if any(telegram_id <= 0 for telegram_id in admin_ids):
            raise ConfigurationError("BOT_ADMIN_IDS must contain positive Telegram IDs")
        if not admin_ids:
            raise ConfigurationError("BOT_ADMIN_IDS must contain at least one administrator")
        raw_owner_id = os.environ.get("BOT_OWNER_ID", "").strip()
        try:
            owner_id = int(raw_owner_id) if raw_owner_id else None
        except ValueError as error:
            raise ConfigurationError("BOT_OWNER_ID must be an integer") from error
        if owner_id is not None and owner_id not in admin_ids:
            raise ConfigurationError("BOT_OWNER_ID must be included in BOT_ADMIN_IDS")

        squad_uuid = (
            os.environ.get("PANEL_INTERNAL_SQUAD_UUID", "").strip() or None
            if require_panel
            else None
        )
        if squad_uuid is not None:
            try:
                squad_uuid = str(UUID(squad_uuid))
            except ValueError as error:
                raise ConfigurationError(
                    "PANEL_INTERNAL_SQUAD_UUID must be a UUID"
                ) from error

        ca_bundle = (
            os.environ.get("PANEL_CA_BUNDLE", "").strip() or None
            if require_panel
            else None
        )
        if ca_bundle is not None and not Path(ca_bundle).is_file():
            raise ConfigurationError("PANEL_CA_BUNDLE does not point to a file")

        plans_file = (
            os.environ.get("PLANS_FILE", "").strip() or None
            if require_panel
            else None
        )
        if plans_file is not None and not Path(plans_file).is_file():
            raise ConfigurationError("PLANS_FILE does not point to a file")

        support_contact = os.environ.get("SUPPORT_CONTACT", "").strip() or None
        if support_contact is not None and len(support_contact) > 128:
            raise ConfigurationError("SUPPORT_CONTACT is too long")

        trial_channel_username = (
            os.environ.get("TRIAL_CHANNEL_USERNAME", "jopesvpn").strip().lstrip("@")
        )
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", trial_channel_username):
            raise ConfigurationError("TRIAL_CHANNEL_USERNAME is invalid")
        try:
            trial_days = int(os.environ.get("TRIAL_DAYS", "3").strip())
        except ValueError as error:
            raise ConfigurationError("TRIAL_DAYS must be an integer") from error
        if not 1 <= trial_days <= 365:
            raise ConfigurationError("TRIAL_DAYS must be between 1 and 365")

        raw_traffic_plans = os.environ.get(
            "ENABLE_TRAFFIC_PLANS", "0"
        ).strip().lower()
        if raw_traffic_plans not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
            raise ConfigurationError("ENABLE_TRAFFIC_PLANS must be a boolean")

        raw_free_access = os.environ.get("ENABLE_FREE_ACCESS", "0").strip().lower()
        if raw_free_access not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
            raise ConfigurationError("ENABLE_FREE_ACCESS must be a boolean")

        subscription_page_url = (
            os.environ.get("SUBSCRIPTION_PAGE_URL", "").strip().rstrip("/") or None
        )
        if subscription_page_url is not None:
            try:
                parsed_page_url = urlsplit(subscription_page_url)
                parsed_page_url.port
            except ValueError as error:
                raise ConfigurationError("SUBSCRIPTION_PAGE_URL is invalid") from error
            if (
                parsed_page_url.scheme != "https"
                or not parsed_page_url.hostname
                or parsed_page_url.username is not None
                or parsed_page_url.password is not None
                or parsed_page_url.query
                or parsed_page_url.fragment
                or parsed_page_url.path
            ):
                raise ConfigurationError(
                    "SUBSCRIPTION_PAGE_URL must be an HTTPS origin"
                )

        state_db_path = os.environ.get("BOT_STATE_DB", "").strip() or None
        ticket_portal_url = (
            os.environ.get("TICKET_PORTAL_URL", "").strip().rstrip("/") or None
        )
        if ticket_portal_url is not None:
            try:
                parsed_ticket_url = urlsplit(ticket_portal_url)
                parsed_ticket_url.port
            except ValueError as error:
                raise ConfigurationError("TICKET_PORTAL_URL is invalid") from error
            if (
                parsed_ticket_url.scheme != "https"
                or not parsed_ticket_url.hostname
                or parsed_ticket_url.username is not None
                or parsed_ticket_url.password is not None
                or parsed_ticket_url.query
                or parsed_ticket_url.fragment
                or parsed_ticket_url.path != "/support"
            ):
                raise ConfigurationError(
                    "TICKET_PORTAL_URL must be an HTTPS /support URL"
                )

        return cls(
            bot_token=bot_token,
            panel_api_url=panel_api_url,
            panel_api_token=panel_api_token,
            admin_telegram_ids=frozenset(admin_ids),
            panel_internal_squad_uuid=squad_uuid,
            panel_ca_bundle=ca_bundle,
            plans_file=plans_file,
            support_contact=support_contact,
            trial_channel_username=trial_channel_username,
            trial_days=trial_days,
            traffic_plans_enabled=raw_traffic_plans in {"1", "true", "yes", "on"},
            free_access_enabled=raw_free_access in {"1", "true", "yes", "on"},
            subscription_page_url=subscription_page_url,
            ticket_portal_url=ticket_portal_url,
            state_db_path=state_db_path,
            owner_telegram_id=owner_id,
        )
