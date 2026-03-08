from __future__ import annotations

import unittest
import tempfile
import json
import os
from types import SimpleNamespace

from src.app.handlers.services.gmail.ingest_handler import GmailIngestHandler
from src.integrations.gmail_service import ParsedEmail


class GmailIngestHandlerTests(unittest.TestCase):
    def _make_bot(self, accounts_json: str) -> SimpleNamespace:
        settings = SimpleNamespace(
            gmail_accounts_json=accounts_json,
            gmail_enabled=True,
            gmail_global_query="label:inbox",
            gmail_use_llm_importance=False,
            gmail_require_rule_match=True,
            gmail_summary_enabled=True,
            gmail_delivery_mode="batch",
            gmail_batch_interval_minutes=15,
            gmail_urgent_score_threshold=0.85,
            gmail_thread_cooldown_minutes=90,
            gmail_vip_sender_score_boost=0.45,
            gmail_trusted_domain_score_boost=0.25,
            gmail_system_sender_score_boost=0.15,
            gmail_attachment_score_boost=0.2,
            personal_chat_id=123,
        )
        return SimpleNamespace(settings=settings)

    def test_parse_accounts_from_json(self) -> None:
        bot = self._make_bot(
            '[{"account_id":"work","credentials_file":"a.json","token_file":"t.json","query":"is:unread"}]'
        )
        handler = GmailIngestHandler(bot)
        self.assertEqual(handler.account_ids(), ["work"])

    def test_rule_classifier_marks_allowlist_and_keyword_as_important(self) -> None:
        bot = self._make_bot(
            '[{"account_id":"work","credentials_file":"a.json","token_file":"t.json",'
            '"sender_allowlist":["boss@company.com"],"keyword_include":["deadline"]}]'
        )
        handler = GmailIngestHandler(bot)
        account = handler._parse_accounts(bot.settings.gmail_accounts_json)[0]
        parsed = ParsedEmail(
            gmail_message_id="m1",
            thread_id="t1",
            from_email="Boss <boss@company.com>",
            subject="Deadline for contract",
            snippet="Please finish by Friday",
            body_text="Action required before Friday deadline.",
            label_ids=[],
            internal_date_utc="2026-01-01T00:00:00+00:00",
            links=[],
            has_attachments=False,
            attachment_names=[],
        )
        important, score, reason = handler._classify_by_rules(account, parsed)
        self.assertTrue(important)
        self.assertGreaterEqual(score, 0.6)
        self.assertIn("allowlist", reason)

    def test_hybrid_mode_only_sends_high_score_immediately(self) -> None:
        bot = self._make_bot("[]")
        bot.settings.gmail_delivery_mode = "hybrid"
        bot.settings.gmail_require_rule_match = True
        handler = GmailIngestHandler(bot)
        self.assertFalse(handler._should_send_immediately(0.84))
        self.assertTrue(handler._should_send_immediately(0.86))

    def test_require_rule_match_blocks_llm_fallback(self) -> None:
        bot = self._make_bot(
            '[{"account_id":"work","credentials_file":"a.json","token_file":"t.json"}]'
        )
        bot.settings.gmail_require_rule_match = True
        handler = GmailIngestHandler(bot)
        account = handler._parse_accounts(bot.settings.gmail_accounts_json)[0]
        parsed = ParsedEmail(
            gmail_message_id="m2",
            thread_id="t2",
            from_email="newsletter@example.com",
            subject="Weekly digest",
            snippet="No action needed",
            body_text="General updates only.",
            label_ids=[],
            internal_date_utc="2026-01-01T00:00:00+00:00",
            links=[],
            has_attachments=False,
            attachment_names=[],
        )

        import asyncio

        important, score, reason = asyncio.run(handler._classify_importance(account, parsed))
        self.assertFalse(important)
        self.assertLess(score, 0.6)
        self.assertIn("rule gate", reason)

    def test_vip_and_trusted_domain_bypass_rule_scoring(self) -> None:
        bot = self._make_bot(
            '[{"account_id":"work","credentials_file":"a.json","token_file":"t.json",'
            '"sender_vip":["ceo@company.com"],"sender_trusted_domains":["company.com"],'
            '"attachment_keyword_include":["invoice"]}]'
        )
        handler = GmailIngestHandler(bot)
        account = handler._parse_accounts(bot.settings.gmail_accounts_json)[0]
        parsed = ParsedEmail(
            gmail_message_id="m3",
            thread_id="t3",
            from_email="CEO <ceo@company.com>",
            subject="Monthly finance review",
            snippet="Attached invoice for approval",
            body_text="Please review.",
            label_ids=[],
            internal_date_utc="2026-01-01T00:00:00+00:00",
            links=[],
            has_attachments=True,
            attachment_names=["invoice_jan.pdf"],
        )
        important, score, reason = handler._classify_by_rules(account, parsed)
        self.assertTrue(important)
        self.assertEqual(score, 1.0)
        self.assertIn("vip sender whitelist", reason)
        self.assertIn("trusted domain whitelist", reason)

    def test_minimum_content_guard(self) -> None:
        bot = self._make_bot(
            '[{"account_id":"work","credentials_file":"a.json","token_file":"t.json"}]'
        )
        handler = GmailIngestHandler(bot)
        parsed = ParsedEmail(
            gmail_message_id="m4",
            thread_id="t4",
            from_email="x@y.com",
            subject="Hi",
            snippet="ok",
            body_text="ok",
            label_ids=[],
            internal_date_utc="2026-01-01T00:00:00+00:00",
            links=[],
            has_attachments=False,
            attachment_names=[],
        )
        self.assertFalse(handler._has_minimum_content(parsed))

    def test_fallback_summary_contains_links(self) -> None:
        bot = self._make_bot("[]")
        handler = GmailIngestHandler(bot)
        parsed = ParsedEmail(
            gmail_message_id="m5",
            thread_id="t5",
            from_email="x@y.com",
            subject="Action needed",
            snippet="Please review",
            body_text="Please review this item.",
            label_ids=[],
            internal_date_utc="2026-01-01T00:00:00+00:00",
            links=["https://example.com/a"],
            has_attachments=False,
            attachment_names=[],
        )
        summary = handler._build_fallback_summary(parsed)
        self.assertIn("Links:", summary)
        self.assertIn("https://example.com/a", summary)

    def test_risky_link_and_urgent_subject_increase_rule_score(self) -> None:
        bot = self._make_bot(
            '[{"account_id":"work","credentials_file":"a.json","token_file":"t.json"}]'
        )
        handler = GmailIngestHandler(bot)
        account = handler._parse_accounts(bot.settings.gmail_accounts_json)[0]
        parsed = ParsedEmail(
            gmail_message_id="m6",
            thread_id="t6",
            from_email="alerts@mailer.example",
            subject="Urgent action required: verify now",
            snippet="Security team request",
            body_text="Please click here and verify your account.",
            label_ids=[],
            internal_date_utc="2026-01-01T00:00:00+00:00",
            links=["https://bit.ly/secure-reset"],
            has_attachments=False,
            attachment_names=[],
        )
        important, score, reason = handler._classify_by_rules(account, parsed)
        self.assertTrue(important)
        self.assertGreaterEqual(score, 0.6)
        self.assertIn("risky link domain", reason)

    def test_promotional_message_stays_not_important(self) -> None:
        bot = self._make_bot(
            '[{"account_id":"work","credentials_file":"a.json","token_file":"t.json"}]'
        )
        handler = GmailIngestHandler(bot)
        account = handler._parse_accounts(bot.settings.gmail_accounts_json)[0]
        parsed = ParsedEmail(
            gmail_message_id="m7",
            thread_id="t7",
            from_email="newsletter@updates.example",
            subject="Weekly newsletter",
            snippet="Latest updates",
            body_text="Read this newsletter in browser and unsubscribe any time.",
            label_ids=[],
            internal_date_utc="2026-01-01T00:00:00+00:00",
            links=[],
            has_attachments=False,
            attachment_names=[],
        )
        important, score, reason = handler._classify_by_rules(account, parsed)
        self.assertFalse(important)
        self.assertLess(score, 0.6)
        self.assertIn("promotional pattern", reason)

    def test_account_specific_filter_phrases_and_domains_override_defaults(self) -> None:
        bot = self._make_bot(
            '[{"account_id":"work","credentials_file":"a.json","token_file":"t.json",'
            '"shortener_domains":["safe.example"],'
            '"suspicious_phrases":["project zebra"],'
            '"urgent_subject_phrases":["needs review"],'
            '"risky_tlds":[]}]'
        )
        handler = GmailIngestHandler(bot)
        account = handler._parse_accounts(bot.settings.gmail_accounts_json)[0]
        parsed = ParsedEmail(
            gmail_message_id="m8",
            thread_id="t8",
            from_email="alerts@internal.company.com",
            subject="Needs review by EOD",
            snippet="Please check",
            body_text="Project Zebra has an update. Please review.",
            label_ids=[],
            internal_date_utc="2026-01-01T00:00:00+00:00",
            links=["https://safe.example/path"],
            has_attachments=False,
            attachment_names=[],
        )
        important, score, reason = handler._classify_by_rules(account, parsed)
        self.assertTrue(important)
        self.assertGreaterEqual(score, 0.6)
        self.assertIn("risky link domain", reason)
        self.assertIn("suspicious phrase with link", reason)
        self.assertIn("urgent subject with link", reason)

    def test_filter_keys_file_overrides_account_filter_lists(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(
                {
                    "shortener_domains": ["safe.example"],
                    "suspicious_phrases": ["project zebra"],
                    "urgent_subject_phrases": ["needs review"],
                    "promotional_phrases": ["digest"],
                },
                f,
            )
            filter_path = f.name

        try:
            bot = self._make_bot(
                '[{"account_id":"work","credentials_file":"a.json","token_file":"t.json",'
                f'"filter_keys_file":"{filter_path.replace("\\", "\\\\")}"' +
                '}]'
            )
            handler = GmailIngestHandler(bot)
            account = handler._parse_accounts(bot.settings.gmail_accounts_json)[0]

            parsed = ParsedEmail(
                gmail_message_id="m9",
                thread_id="t9",
                from_email="alerts@internal.company.com",
                subject="Needs review today",
                snippet="Please check",
                body_text="Project Zebra update.",
                label_ids=[],
                internal_date_utc="2026-01-01T00:00:00+00:00",
                links=["https://safe.example/path"],
                has_attachments=False,
                attachment_names=[],
            )
            important, score, reason = handler._classify_by_rules(account, parsed)
            self.assertTrue(important)
            self.assertGreaterEqual(score, 0.6)
            self.assertIn("risky link domain", reason)
        finally:
            os.unlink(filter_path)


if __name__ == "__main__":
    unittest.main()
