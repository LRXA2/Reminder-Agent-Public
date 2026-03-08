from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from src.integrations.gmail_service import ParsedEmail


@dataclass(frozen=True)
class GmailRuleResult:
    important: bool
    score: float
    reason: str


class GmailLightFilter:
    """Lightweight rule classifier for Gmail triage.

    This intentionally keeps only high-signal, low-cost checks.
    """

    _DEFAULT_RISKY_TLDS = ("zip", "click", "top", "xyz", "mov")
    _DEFAULT_SHORTENER_DOMAINS = (
        "bit.ly",
        "tinyurl.com",
        "rb.gy",
        "t.co",
        "goo.gl",
        "ow.ly",
        "is.gd",
    )
    _DEFAULT_SUSPICIOUS_PHRASES = (
        "click here",
        "verify your account",
        "password reset",
        "urgent action required",
        "payment failed",
        "bank transfer",
        "claim now",
    )
    _DEFAULT_PROMOTIONAL_PHRASES = (
        "unsubscribe",
        "view in browser",
        "limited time offer",
        "marketing",
        "newsletter",
    )
    _DEFAULT_URGENT_SUBJECT_PHRASES = (
        "urgent",
        "action required",
        "verify",
        "payment",
        "security alert",
        "invoice",
    )

    def classify(self, account: Any, parsed: ParsedEmail, settings: Any) -> GmailRuleResult:
        sender = (parsed.from_email or "").lower()
        sender_email = self._extract_sender_email(sender)
        sender_domain = self._extract_sender_domain(sender_email)
        text_blob = f"{parsed.subject}\n{parsed.snippet}\n{parsed.body_text}".lower()
        attachment_blob = "\n".join(parsed.attachment_names).lower()
        risky_tlds = set(self._pattern_list(getattr(account, "risky_tlds", ()), self._DEFAULT_RISKY_TLDS))
        shortener_domains = set(self._pattern_list(getattr(account, "shortener_domains", ()), self._DEFAULT_SHORTENER_DOMAINS))
        suspicious_phrases = self._pattern_list(getattr(account, "suspicious_phrases", ()), self._DEFAULT_SUSPICIOUS_PHRASES)
        promotional_phrases = self._pattern_list(getattr(account, "promotional_phrases", ()), self._DEFAULT_PROMOTIONAL_PHRASES)
        urgent_subject_phrases = self._pattern_list(
            getattr(account, "urgent_subject_phrases", ()), self._DEFAULT_URGENT_SUBJECT_PHRASES
        )

        if self._matches_sender_block(sender, sender_domain, account.sender_blocklist):
            return GmailRuleResult(False, 0.05, "sender blocked")

        if account.keyword_exclude and self._matches_any(text_blob, account.keyword_exclude):
            return GmailRuleResult(False, 0.1, "excluded keyword")

        vip_match = bool(account.sender_vip and self._matches_any(sender, account.sender_vip))
        trusted_domain_match = bool(
            sender_domain
            and account.sender_trusted_domains
            and self._matches_domain(sender_domain, account.sender_trusted_domains)
        )
        if vip_match or trusted_domain_match:
            reasons: list[str] = []
            if vip_match:
                reasons.append("vip sender whitelist")
            if trusted_domain_match:
                reasons.append("trusted domain whitelist")
            return GmailRuleResult(True, 1.0, ", ".join(reasons))

        score = 0.2
        reasons: list[str] = []

        if account.sender_system and self._matches_any(sender, account.sender_system):
            score += float(getattr(settings, "gmail_system_sender_score_boost", 0.15))
            reasons.append("system sender")

        if account.sender_allowlist and self._matches_any(sender, account.sender_allowlist):
            score += 0.45
            reasons.append("sender allowlist")

        if account.keyword_include and self._matches_any(text_blob, account.keyword_include):
            score += 0.4
            reasons.append("include keyword")

        if "IMPORTANT" in parsed.label_ids:
            score += 0.25
            reasons.append("gmail important label")

        if parsed.has_attachments:
            score += float(getattr(settings, "gmail_attachment_score_boost", 0.2))
            reasons.append("has attachment")

        if account.attachment_keyword_include and self._matches_any(attachment_blob, account.attachment_keyword_include):
            score += 0.25
            reasons.append("attachment keyword")

        link_domains = self._extract_link_domains(parsed.links)
        risky_link = self._has_risky_link(link_domains, risky_tlds, shortener_domains)
        if risky_link:
            score += 0.2
            reasons.append("risky link domain")

        suspicious_phrase = self._first_match(text_blob, suspicious_phrases)
        if suspicious_phrase and parsed.links:
            score += 0.2
            reasons.append("suspicious phrase with link")

        urgent_subject = self._first_match((parsed.subject or "").lower(), urgent_subject_phrases)
        if urgent_subject and parsed.links:
            score += 0.15
            reasons.append("urgent subject with link")

        promo_hint = self._first_match(text_blob, promotional_phrases)
        if promo_hint and not parsed.has_attachments:
            score -= 0.2
            reasons.append("promotional pattern")

        score = max(0.0, min(1.0, score))
        important = score >= 0.6
        reason = ", ".join(reasons) if reasons else "no high-signal rules matched"
        return GmailRuleResult(important, score, reason)

    def _matches_sender_block(self, sender: str, sender_domain: str, patterns: tuple[str, ...]) -> bool:
        if not patterns:
            return False
        if self._matches_any(sender, patterns):
            return True
        if sender_domain and self._matches_domain(sender_domain, patterns):
            return True
        return False

    @staticmethod
    def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
        lowered = (text or "").lower()
        for pattern in patterns:
            token = pattern.strip().lower()
            if not token:
                continue
            if token.startswith("@"):
                if token in lowered:
                    return True
            elif token in lowered:
                return True
        return False

    @staticmethod
    def _matches_domain(domain: str, domains: tuple[str, ...]) -> bool:
        current = domain.strip().lower()
        if not current:
            return False
        for raw in domains:
            item = raw.strip().lower().lstrip("@")
            if not item:
                continue
            if current == item or current.endswith("." + item):
                return True
        return False

    @staticmethod
    def _extract_sender_email(sender: str) -> str:
        text = (sender or "").strip().lower()
        match = re.search(r"<([^>]+@[^>]+)>", text)
        if match:
            return match.group(1).strip()
        fallback = re.search(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", text)
        if fallback:
            return fallback.group(0).strip()
        return text

    @staticmethod
    def _extract_sender_domain(sender_email: str) -> str:
        email = (sender_email or "").strip().lower()
        if "@" not in email:
            return ""
        return email.split("@", 1)[1].strip()

    @staticmethod
    def _extract_link_domains(links: list[str]) -> list[str]:
        domains: list[str] = []
        for link in links:
            try:
                netloc = urlsplit(link).netloc.lower().strip()
            except Exception:
                continue
            if not netloc:
                continue
            if netloc.startswith("www."):
                netloc = netloc[4:]
            if netloc not in domains:
                domains.append(netloc)
        return domains

    def _has_risky_link(self, domains: list[str], risky_tlds: set[str], shortener_domains: set[str]) -> bool:
        for domain in domains:
            root = domain.split(":", 1)[0].strip().lower()
            if not root:
                continue
            if root in shortener_domains:
                return True
            if "xn--" in root:
                return True
            if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", root):
                return True
            tld = root.rsplit(".", 1)[-1] if "." in root else ""
            if tld in risky_tlds:
                return True
        return False

    @staticmethod
    def _pattern_list(override: tuple[str, ...], default: tuple[str, ...]) -> tuple[str, ...]:
        values = tuple(token.strip().lower() for token in override if token and token.strip())
        return values or default

    @staticmethod
    def _first_match(text: str, patterns: tuple[str, ...]) -> str:
        lowered = (text or "").lower()
        for item in patterns:
            if item in lowered:
                return item
        return ""
