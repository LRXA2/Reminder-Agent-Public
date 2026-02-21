from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentRef:
    kind: str
    file_id: str
    mime_type: str
    file_name: str
