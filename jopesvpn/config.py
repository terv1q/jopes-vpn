from __future__ import annotations

import os
import re
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    pass


DEFAULT_COMMUNITY_URL = "https://t.me/JopesVpnChat"
DEFAULT_REVIEWS_URL = "https://t.me/JopesVpnReviews"
DEFAULT_RESOURCES_URL = "https://t.me/addlist/cEJd4DRCx25kMmEy"


@dataclass(frozen=True)
class PromoPolicy:
    min_code_length: int = 8
    max_code_length: int = 48
    max_attempts: int = 5
    global_max_attempts: int = 300
    attempt_window_seconds: int = 900
    max_reward_minor: int = 100_000
    max_valid_days: int = 365
    max_uses: int = 10_000

    def __post_init__(self) -> None:
        if (
            not 4 <= self.min_code_length <= self.max_code_length <= 64
            or not 1 <= self.max_attempts <= self.global_max_attempts <= 100_000
            or not 1 <= self.attempt_window_seconds <= 86_400
            or not 1 <= self.max_reward_minor <= 100_000_000
            or not 1 <= self.max_valid_days <= 3650
            or not 1 <= self.max_uses <= 1_000_000
        ):
            raise ConfigurationError("Invalid promo code limits")

    @classmethod
    def from_env(cls) -> PromoPolicy:
        defaults = cls()
        try:
            return cls(**{
                name: int(os.environ.get(f"PROMO_{name.upper()}", str(value)))
                for name, value in vars(defaults).items()
            })
        except ValueError as error:
            raise ConfigurationError("PROMO limits must be valid integers") from error


@dataclass(frozen=True)
class ServicePolicy:
    poll_seconds: int = 300
    device_scan_seconds: int = 3600
    support_reminder_hours: int = 2
    repeat_trial_threshold: int = 3
    referral_daily_threshold: int = 5
    ticket_retention_days: int = 90
    event_retention_days: int = 30
    summary_hour_utc: int = 7

    @classmethod
    def from_env(cls) -> ServicePolicy:
        defaults = cls()
        try:
            result = cls(**{
                key: int(os.environ.get(f"OPS_{key.upper()}", str(value)))
                for key, value in vars(defaults).items()
            })
        except ValueError as error:
            raise ConfigurationError("OPS limits must be integers") from error
        if (
            not 60 <= result.poll_seconds <= 3600
            or not 300 <= result.device_scan_seconds <= 86400
            or not 1 <= result.support_reminder_hours <= 168
            or not 2 <= result.repeat_trial_threshold <= 100
            or not 2 <= result.referral_daily_threshold <= 10000
            or not 1 <= result.ticket_retention_days <= 3650
            or not 1 <= result.event_retention_days <= 365
            or not 0 <= result.summary_hour_utc <= 23
        ):
            raise ConfigurationError("OPS limits are out of range")
        return result


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
    platega_merchant_id: str | None = None
    platega_secret: str | None = None
    platega_trusted_proxy_ip: str | None = None
    brand_name: str = "Jopes VPN"
    community_url: str | None = DEFAULT_COMMUNITY_URL
    reviews_url: str | None = DEFAULT_REVIEWS_URL
    resources_url: str | None = DEFAULT_RESOURCES_URL
    promo_policy: PromoPolicy = PromoPolicy()
    health_probe_interval_seconds: int = 60
    health_probe_max_age_seconds: int = 180
    service_policy: ServicePolicy = ServicePolicy()

    @property
    def service_owner_id(self) -> int | None:
        if self.owner_telegram_id is not None:
            return self.owner_telegram_id
        return next(iter(self.admin_telegram_ids)) if len(self.admin_telegram_ids) == 1 else None

    @property
    def platega_enabled(self) -> bool:
        return bool(self.platega_merchant_id and self.platega_secret)

    @property
    def platega_callback_url(self) -> str | None:
        if not self.platega_enabled or self.ticket_portal_url is None:
            return None
        parsed = urlsplit(self.ticket_portal_url)
        return f"{parsed.scheme}://{parsed.netloc}/platega/callback"

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
                _ = parsed_panel_url.port
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
                _ = parsed_page_url.port
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
                _ = parsed_ticket_url.port
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

        merchant_id = os.environ.get("PLATEGA_MERCHANT_ID", "").strip() or None
        platega_secret = os.environ.get("PLATEGA_SECRET", "").strip() or None
        if bool(merchant_id) != bool(platega_secret):
            raise ConfigurationError("Set both PLATEGA_MERCHANT_ID and PLATEGA_SECRET")
        if merchant_id is not None:
            try:
                merchant_id = str(UUID(merchant_id))
            except ValueError as error:
                raise ConfigurationError("PLATEGA_MERCHANT_ID must be a UUID") from error
            if (
                not platega_secret or not platega_secret.isascii()
                or len(platega_secret) > 4096
                or any(ord(char) < 33 or ord(char) == 127 for char in platega_secret)
            ):
                raise ConfigurationError("PLATEGA_SECRET must be a valid API key")
            if ticket_portal_url is None:
                raise ConfigurationError(
                    "TICKET_PORTAL_URL is required for Platega callbacks"
                )

        trusted_proxy_ip = os.environ.get("PLATEGA_TRUSTED_PROXY_IP", "").strip() or None
        if trusted_proxy_ip is not None:
            try:
                trusted_proxy_ip = str(ip_address(trusted_proxy_ip))
            except ValueError as error:
                raise ConfigurationError("PLATEGA_TRUSTED_PROXY_IP must be an IP address") from error

        brand_name = os.environ.get("BRAND_NAME", "Jopes VPN").strip()
        if not brand_name or len(brand_name) > 64 or any(ord(c) < 32 for c in brand_name):
            raise ConfigurationError("BRAND_NAME must contain 1 to 64 printable characters")
        resource_urls = {}
        for name, default in (
            ("community_url", DEFAULT_COMMUNITY_URL),
            ("reviews_url", DEFAULT_REVIEWS_URL),
            ("resources_url", DEFAULT_RESOURCES_URL),
        ):
            value = os.environ.get(name.upper(), default).strip() or None
            if value is not None:
                try:
                    parsed = urlsplit(value)
                    _ = parsed.port
                except ValueError as error:
                    raise ConfigurationError(f"{name.upper()} is invalid") from error
                if (
                    parsed.scheme != "https" or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value)
                ):
                    raise ConfigurationError(f"{name.upper()} must be an HTTPS URL")
            resource_urls[name] = value

        try:
            health_interval = int(os.environ.get("HEALTH_PROBE_INTERVAL_SECONDS", "60"))
            health_max_age = int(os.environ.get("HEALTH_PROBE_MAX_AGE_SECONDS", "180"))
        except ValueError as error:
            raise ConfigurationError("HEALTH_PROBE limits must be integers") from error
        if not 5 <= health_interval <= 3600 or not 2 * health_interval <= health_max_age <= 86400:
            raise ConfigurationError("Invalid HEALTH_PROBE interval or maximum age")

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
            platega_merchant_id=merchant_id,
            platega_secret=platega_secret,
            platega_trusted_proxy_ip=trusted_proxy_ip,
            brand_name=brand_name,
            promo_policy=PromoPolicy.from_env(),
            health_probe_interval_seconds=health_interval,
            health_probe_max_age_seconds=health_max_age,
            service_policy=ServicePolicy.from_env(),
            **resource_urls,
        )
