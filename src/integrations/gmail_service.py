from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


@dataclass(frozen=True)
class ParsedEmail:
    gmail_message_id: str
    thread_id: str
    from_email: str
    subject: str
    snippet: str
    body_text: str
    label_ids: list[str]
    internal_date_utc: str
    links: list[str]
    has_attachments: bool
    attachment_names: list[str]


class GmailService:
    def __init__(self, account_id: str, credentials_file: str, token_file: str) -> None:
        self.account_id = account_id
        self.credentials_file = credentials_file
        self.token_file = token_file
        self._service = None
        self._service_error = ""

    def is_ready(self) -> bool:
        return self._get_service() is not None

    def get_last_error(self) -> str:
        return self._service_error

    def list_message_ids(self, query: str, max_results: int = 50) -> list[str]:
        service = self._get_service()
        if service is None:
            return []

        ids: list[str] = []
        page_token: str | None = None
        remaining = max(1, min(200, max_results))
        while remaining > 0:
            page_size = min(100, remaining)
            response = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=page_size,
                    pageToken=page_token,
                    includeSpamTrash=False,
                )
                .execute()
            )
            for item in response.get("messages") or []:
                message_id = str(item.get("id") or "").strip()
                if message_id:
                    ids.append(message_id)
            page_token = response.get("nextPageToken")
            if not page_token:
                break
            remaining = max_results - len(ids)
        return ids

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        service = self._get_service()
        if service is None:
            return None
        try:
            message = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            if isinstance(message, dict):
                return message
            return None
        except Exception as exc:
            LOGGER.warning("Gmail get_message failed account=%s id=%s error=%s", self.account_id, message_id, exc)
            return None

    def extract_email_payload(self, message: dict[str, Any]) -> ParsedEmail:
        payload_obj = message.get("payload")
        payload: dict[str, Any] = payload_obj if isinstance(payload_obj, dict) else {}
        headers_obj = payload.get("headers")
        headers: list[dict[str, Any]] = []
        if isinstance(headers_obj, list):
            headers = [item for item in headers_obj if isinstance(item, dict)]

        from_email = self._extract_header(headers, "From")
        subject = self._extract_header(headers, "Subject")
        snippet = str(message.get("snippet") or "").strip()
        label_ids = [str(item) for item in (message.get("labelIds") or []) if str(item).strip()]

        body_text = self._extract_message_body_text(payload)
        body_text = self._extract_new_content(body_text)
        if not body_text:
            body_text = snippet

        links = self._extract_links(f"{subject}\n{snippet}\n{body_text}")
        attachment_names = self._extract_attachment_names(payload)

        internal_date_utc = self._parse_internal_date_utc(message.get("internalDate"))
        return ParsedEmail(
            gmail_message_id=str(message.get("id") or "").strip(),
            thread_id=str(message.get("threadId") or "").strip(),
            from_email=from_email,
            subject=subject,
            snippet=snippet,
            body_text=body_text,
            label_ids=label_ids,
            internal_date_utc=internal_date_utc,
            links=links,
            has_attachments=bool(attachment_names),
            attachment_names=attachment_names,
        )

    def _get_service(self):
        if self._service is not None:
            return self._service

        credentials_path = Path(self.credentials_file)
        if not credentials_path.exists():
            self._service_error = f"credentials file not found: {credentials_path}"
            return None

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except Exception as exc:
            self._service_error = f"google client libraries unavailable: {exc}"
            return None

        token_path = Path(self.token_file)
        creds = None
        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), [GMAIL_READONLY_SCOPE])
            except Exception:
                creds = None

        try:
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), [GMAIL_READONLY_SCOPE])
                    creds = flow.run_local_server(port=0)
                token_path.parent.mkdir(parents=True, exist_ok=True)
                token_path.write_text(creds.to_json(), encoding="utf-8")

            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            self._service_error = ""
            return self._service
        except Exception as exc:
            self._service_error = f"gmail service initialization failed: {exc}"
            return None

    def _extract_header(self, headers: list[dict[str, Any]], key: str) -> str:
        target = key.strip().lower()
        for item in headers:
            name = str(item.get("name") or "").strip().lower()
            if name == target:
                return str(item.get("value") or "").strip()
        return ""

    def _extract_message_body_text(self, payload: dict[str, Any]) -> str:
        mime_type = str(payload.get("mimeType") or "").lower()
        if mime_type.startswith("text/"):
            data = self._decode_body_data(payload)
            if mime_type == "text/html":
                return self._strip_html(data)
            return data

        parts_obj = payload.get("parts")
        parts = parts_obj if isinstance(parts_obj, list) else []
        plain_text_parts: list[str] = []
        html_parts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            nested = self._extract_message_body_text(part)
            nested_mime = str(part.get("mimeType") or "").lower()
            if not nested:
                continue
            if nested_mime == "text/plain":
                plain_text_parts.append(nested)
            elif nested_mime == "text/html":
                html_parts.append(nested)
            else:
                plain_text_parts.append(nested)

        if plain_text_parts:
            return "\n".join(text for text in plain_text_parts if text).strip()
        if html_parts:
            return "\n".join(text for text in html_parts if text).strip()
        return ""

    def _decode_body_data(self, part: dict[str, Any]) -> str:
        body_obj = part.get("body")
        body = body_obj if isinstance(body_obj, dict) else {}
        raw_data = str(body.get("data") or "")
        if not raw_data:
            return ""
        try:
            padded = raw_data + "=" * (-len(raw_data) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
            return decoded.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    def _strip_html(self, html: str) -> str:
        if not html:
            return ""
        text = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _extract_links(self, text: str) -> list[str]:
        links: list[str] = []
        for raw in re.findall(r"https?://\S+", text or ""):
            cleaned = raw.rstrip(").,]>")
            if cleaned and cleaned not in links:
                links.append(cleaned)
        return links

    def _parse_internal_date_utc(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return datetime.now(timezone.utc).isoformat()
        try:
            ts_ms = int(raw)
            return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            return datetime.now(timezone.utc).isoformat()

    def _extract_attachment_names(self, payload: dict[str, Any]) -> list[str]:
        names: list[str] = []

        def walk(part: dict[str, Any]) -> None:
            filename = str(part.get("filename") or "").strip()
            body_obj = part.get("body")
            body = body_obj if isinstance(body_obj, dict) else {}
            attachment_id = str(body.get("attachmentId") or "").strip()
            if filename and (attachment_id or self._looks_like_attachment(filename)) and filename not in names:
                names.append(filename)
            children_obj = part.get("parts")
            children = children_obj if isinstance(children_obj, list) else []
            for child in children:
                if isinstance(child, dict):
                    walk(child)

        walk(payload)
        return names

    def _looks_like_attachment(self, filename: str) -> bool:
        lower = filename.lower()
        return any(
            lower.endswith(ext)
            for ext in (
                ".pdf",
                ".doc",
                ".docx",
                ".xls",
                ".xlsx",
                ".csv",
                ".zip",
                ".png",
                ".jpg",
                ".jpeg",
                ".txt",
            )
        )

    def _extract_new_content(self, text: str) -> str:
        raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not raw.strip():
            return ""

        cleaned_lines: list[str] = []
        reply_markers = (
            r"^on .+wrote:$",
            r"^from:\s",
            r"^sent:\s",
            r"^subject:\s",
            r"^to:\s",
            r"^---+\s*original message\s*---+$",
            r"^--\s*$",
            r"^sent from my\s",
        )

        for line in raw.split("\n"):
            stripped = line.strip()
            if not stripped:
                cleaned_lines.append("")
                continue
            if stripped.startswith(">"):
                continue
            if any(re.match(pattern, stripped, flags=re.IGNORECASE) for pattern in reply_markers):
                break
            cleaned_lines.append(line)

        joined = "\n".join(cleaned_lines)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        joined = re.sub(r"[ \t]+", " ", joined)
        return joined.strip()
