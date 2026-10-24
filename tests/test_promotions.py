from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from jopesvpn.bot import channel_post_approval
from jopesvpn.config import Settings
from jopesvpn.promotions import PromotionStore
from jopesvpn.tickets import TicketStore


def channel_post_status(tickets: TicketStore, post_id: int) -> str:
    post = tickets.get_channel_post(post_id)
    if post is None:
        raise AssertionError("Channel post was not saved")
    return str(post["status"])


class PromotionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "state.sqlite3"
        self.tickets = TicketStore(self.db_path)
        self.promotions = PromotionStore(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_campaign(self) -> int:
        post_id = self.tickets.create_channel_post(
            owner_id=1001,
            body_html="Акция",
            buttons=[],
            photo_data=None,
            photo_mime=None,
            reviewer_id=1001,
            post_type="promo",
        )
        self.promotions.create_campaign(post_id)
        return post_id

    def publish_campaign(self, post_id: int) -> None:
        self.assertFalse(self.promotions.store_start_request(2000, post_id))
        self.assertTrue(self.tickets.claim_channel_post_preview(post_id))
        self.assertTrue(self.tickets.finish_channel_post_preview(post_id, 44))
        self.assertTrue(self.tickets.claim_channel_post_publication(post_id, 1001))
        self.assertTrue(self.promotions.finish_publication(post_id, 55))
        self.assertEqual(channel_post_status(self.tickets, post_id), "published")

    def test_reward_quotas_and_account_uniqueness(self) -> None:
        post_id = self.create_campaign()
        self.publish_campaign(post_id)

        for telegram_id in range(2001, 2006):
            self.assertEqual(
                self.promotions.reserve_reward(post_id, telegram_id, "new"),
                ("claimed", "new"),
            )
            self.assertTrue(
                self.promotions.finish_reward(telegram_id, post_id, applied=True)
            )
        self.assertEqual(
            self.promotions.reserve_reward(post_id, 2006, "new"), ("full", None)
        )
        self.assertEqual(
            self.promotions.reserve_reward(post_id, 2001, "new"),
            ("applied", "new"),
        )
        self.assertEqual(
            self.promotions.reserve_reward(post_id, 2001, "returning"),
            ("applied", "new"),
        )

        for telegram_id in range(3001, 3004):
            self.assertEqual(
                self.promotions.reserve_reward(post_id, telegram_id, "returning"),
                ("claimed", "returning"),
            )
            self.assertTrue(
                self.promotions.finish_reward(telegram_id, post_id, applied=True)
            )
        self.assertEqual(
            self.promotions.reserve_reward(post_id, 3004, "returning"),
            ("full", None),
        )

    def test_concurrent_claims_cannot_exceed_new_user_quota(self) -> None:
        post_id = self.create_campaign()
        self.publish_campaign(post_id)

        def reserve(telegram_id: int) -> tuple[str, str | None]:
            return self.promotions.reserve_reward(post_id, telegram_id, "new")

        with ThreadPoolExecutor(max_workers=12) as executor:
            outcomes = list(executor.map(reserve, range(4001, 4031)))

        self.assertEqual(sum(outcome[0] == "claimed" for outcome in outcomes), 5)
        self.assertEqual(sum(outcome[0] == "full" for outcome in outcomes), 25)

    def test_campaign_is_not_claimable_until_publication_and_after_cancel(self) -> None:
        post_id = self.create_campaign()
        self.assertEqual(
            self.promotions.reserve_reward(post_id, 5001, "new"),
            ("inactive", None),
        )
        self.assertTrue(self.tickets.claim_channel_post_preview(post_id))
        self.assertTrue(self.tickets.finish_channel_post_preview(post_id, 44))
        self.assertTrue(self.tickets.cancel_channel_post(post_id, 1001))
        self.promotions.cancel_campaign(post_id)
        self.assertFalse(self.promotions.store_start_request(5001, post_id))


class PromotionApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_owner_approval_publishes_and_activates_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.sqlite3"
            tickets = TicketStore(db_path)
            promotions = PromotionStore(db_path)
            post_id = tickets.create_channel_post(
                owner_id=1001,
                body_html="Акция",
                buttons=[],
                photo_data=None,
                photo_mime=None,
                reviewer_id=1001,
                post_type="promo",
            )
            promotions.create_campaign(post_id)
            self.assertTrue(tickets.claim_channel_post_preview(post_id))
            self.assertTrue(tickets.finish_channel_post_preview(post_id, 44))
            settings = Settings(
                bot_token="token",
                panel_api_url="https://panel.example.invalid",
                panel_api_token="panel-token",
                admin_telegram_ids=frozenset({1001}),
                trial_channel_username="officialchannel",
            )

            class FakeBot:
                def __init__(self) -> None:
                    self.sent: list[tuple[str, str]] = []

                async def get_me(self):
                    return SimpleNamespace(username="jopesvpn_bot")

                async def send_message(self, chat_id, text, **kwargs):
                    self.sent.append((chat_id, text))
                    return SimpleNamespace(message_id=55)

            bot = FakeBot()

            async def answer(*args, **kwargs) -> None:
                return None

            async def approve(user_id: int) -> None:
                callback = SimpleNamespace(
                    data=f"channelpost:{post_id}:approve",
                    from_user=SimpleNamespace(id=user_id),
                    message=None,
                    answer=answer,
                )
                await channel_post_approval(
                    callback, bot, settings, tickets, promotions=promotions
                )

            await approve(2002)
            self.assertEqual(channel_post_status(tickets, post_id), "awaiting_approval")
            self.assertEqual(bot.sent, [])

            await approve(1001)
            self.assertEqual(channel_post_status(tickets, post_id), "published")
            self.assertEqual(len(bot.sent), 1)
            self.assertTrue(promotions.store_start_request(5002, post_id))

            await approve(1001)
            self.assertEqual(len(bot.sent), 1)


if __name__ == "__main__":
    unittest.main()
