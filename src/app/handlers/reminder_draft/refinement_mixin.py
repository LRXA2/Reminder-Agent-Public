from __future__ import annotations

import re


class DraftRefinementMixin:
    def _extract_first_url(self, text: str) -> str:
        match = re.search(r"https?://\S+", text)
        if not match:
            return ""
        return match.group(0).rstrip(").,]")

    def _is_valid_link(self, value: str) -> bool:
        return bool(re.match(r"^https?://\S+$", (value or "").strip(), re.IGNORECASE))

    def _refine_generic_title(self, title: str, notes: str, fallback_text: str, confidence: str = "medium") -> str:
        normalized = title.strip()
        lowered = normalized.lower()

        if str(confidence).strip().lower() != "high":
            return normalized

        generic_patterns = (
            r"^(write|send)\s+email\b",
            r"^follow\s*-?up\b",
            r"^(check|review|do|complete|finish|handle)\b",
            r"^(task|reminder)\b",
        )
        is_generic = any(re.search(pattern, lowered) for pattern in generic_patterns)
        if not is_generic:
            return normalized

        combined = f"{notes}\n{fallback_text}"
        match = re.search(
            r"\b(?:on|about|for|regarding)\s+(.+?)(?:\s+\b(by|before|due|at|on)\b|[\.;\n]|$)",
            combined,
            re.IGNORECASE,
        )
        if not match:
            return normalized
        subject = re.sub(r"\s+", " ", match.group(1)).strip(" -:;,.\n\t")
        if not subject:
            return normalized
        subject = re.sub(r"^(the|a|an)\s+", "", subject, flags=re.IGNORECASE)
        if not subject:
            return normalized
        words = [w for w in re.split(r"\s+", subject) if w]
        if len(words) < 2 or len(subject) > 90:
            return normalized

        if "email" in lowered:
            improved = f"Email for {subject}"
        elif "follow" in lowered:
            improved = f"Follow up on {subject}"
        elif "review" in lowered:
            improved = f"Review {subject}"
        elif "check" in lowered:
            improved = f"Check {subject}"
        else:
            improved = f"Action: {subject}"
        return improved[:120]

    def _filter_topics_by_relevance(self, topics: list[str], context_text: str) -> list[str]:
        lowered_context = (context_text or "").lower()
        relevant: list[str] = []
        seen: set[str] = set()
        for topic in topics:
            norm = topic.strip()
            if not norm:
                continue
            key = norm.lower()
            if key in seen:
                continue
            tokens = [token for token in re.split(r"[^a-z0-9]+", key) if token]
            if not tokens:
                continue
            if any(token in lowered_context for token in tokens):
                seen.add(key)
                relevant.append(norm)
        return relevant
