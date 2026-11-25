from __future__ import annotations

import re

from pydantic import ValidationError

from .schemas import ParticipantCreate


def parse_bulk_text(text: str) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    entries: list[dict[str, str]] = []
    errors: list[dict[str, object]] = []
    for index, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = re.split(r"[\s,，]+", line.strip())
        if len(fields) != 2:
            errors.append({"line": index, "reason": "每行请填写显示名和唯一参赛编号"})
            continue
        name, code = fields
        try:
            ParticipantCreate(display_name=name, code=code)
        except ValidationError:
            reason = "显示名限 80 字，编号为 2–40 位字母、数字、下划线或连字符"
            errors.append({"line": index, "reason": reason})
            continue
        entries.append({"display_name": name, "code": code})
    return entries, errors
