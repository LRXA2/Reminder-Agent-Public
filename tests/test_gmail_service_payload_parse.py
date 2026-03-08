from __future__ import annotations

import unittest

from src.integrations.gmail_service import GmailService


class GmailServicePayloadParseTests(unittest.TestCase):
    def test_extract_email_payload_detects_attachments(self) -> None:
        service = GmailService(account_id="work", credentials_file="missing.json", token_file="missing_token.json")
        message = {
            "id": "m1",
            "threadId": "t1",
            "snippet": "See attached file",
            "internalDate": "1760000000000",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "Boss <boss@company.com>"},
                    {"name": "Subject", "value": "Invoice"},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": "U2VlIGF0dGFjaGVk"},
                    },
                    {
                        "mimeType": "application/pdf",
                        "filename": "invoice.pdf",
                        "body": {"attachmentId": "ATTACH-1"},
                    },
                ],
            },
        }

        parsed = service.extract_email_payload(message)
        self.assertTrue(parsed.has_attachments)
        self.assertIn("invoice.pdf", parsed.attachment_names)

    def test_extract_email_payload_strips_quoted_chain_content(self) -> None:
        service = GmailService(account_id="work", credentials_file="missing.json", token_file="missing_token.json")
        message = {
            "id": "m2",
            "threadId": "t2",
            "snippet": "Quick update",
            "internalDate": "1760000000000",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "Boss <boss@company.com>"},
                    {"name": "Subject", "value": "Update"},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {
                            "data": "TmV3IGl0ZW0gdG8gcmV2aWV3LgpPbiBNb24sIEphbiAxMCwgQm9iIHdyb3RlOgo+IE9sZCBxdW90ZWQgdGV4dA=="
                        },
                    }
                ],
            },
        }

        parsed = service.extract_email_payload(message)
        self.assertIn("New item to review", parsed.body_text)
        self.assertNotIn("Old quoted text", parsed.body_text)


if __name__ == "__main__":
    unittest.main()
