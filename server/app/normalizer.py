from __future__ import annotations

import json
from typing import Any

FORBIDDEN_KEYS = {
    "authorization",
    "access_token",
    "refresh_token",
    "benchmark_token",
    "token",
    "cookie",
    "cookies",
    "password",
    "api_key",
    "apikey",
    "vpn_config",
    "benchmark_base_url",
    "base_url",
    "container_addr",
    "sandbox_logs",
    "logs",
    "flag",
    "env_config",
    "app_dist",
    "upload_url",
}
SAFE_FLAG_KEYS = {
    "flag_correct",
    "flag_total",
    "final_flags",
    "flag_index",
    "correct_flag_count",
    "total_flag_count",
}


def _contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_KEYS:
                return normalized
            if "flag" in normalized and normalized not in SAFE_FLAG_KEYS:
                return normalized
            if any(
                part in normalized
                for part in ("token", "cookie", "password", "secret", "credential")
            ):
                return normalized
            found = _contains_forbidden_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _contains_forbidden_key(child)
            if found:
                return found
    return None


def _text(value: Any, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\x00", "")
    if not text:
        return None
    return text[:limit]


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return int(number) if number.is_integer() else round(number, 6)


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _pick(source: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in names:
        if name in source:
            result[name] = source[name]
    return result


TEXT_FIELDS = (
    "status",
    "set_id",
    "set_name",
    "run_source",
    "evaluation_type",
    "username",
    "org",
    "agent_tag",
    "model_name",
    "harness_name",
    "harness_version",
    "created_at",
    "started_at",
    "ended_at",
)

NUMBER_FIELDS = (
    "run_id",
    "current_score",
    "max_score",
    "final_score",
    "score_percent",
    "score_origin",
    "accuracy",
    "flag_correct",
    "flag_total",
    "final_flags",
    "challenge_solved",
    "challenge_total",
    "duration_seconds",
    "elapsed_seconds",
    "score_elapsed_seconds",
    "pending_remain_seconds",
)


def _normalize_base(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in TEXT_FIELDS:
        if field in payload:
            value = _text(payload[field], 160)
            if value is not None:
                result[field] = value
    for field in NUMBER_FIELDS:
        if field in payload:
            value = _number(payload[field])
            if value is not None:
                result[field] = value

    models = payload.get("base_model")
    if isinstance(models, list):
        clean_models = [_text(item, 80) for item in models[:16]]
        result["base_model"] = [item for item in clean_models if item]

    radar = payload.get("radar_data")
    if isinstance(radar, dict):
        clean_radar: dict[str, float | int] = {}
        for key, value in list(radar.items())[:16]:
            clean_key = _text(key, 80)
            clean_value = _number(value)
            if clean_key and clean_value is not None:
                clean_radar[clean_key] = clean_value
        result["radar_data"] = clean_radar

    result["score_events"] = _normalize_score_events(payload.get("score_events"))
    result["run_events"] = _normalize_run_events(payload.get("run_events"))
    result["matrix"] = _normalize_matrix(payload.get("matrix"))
    return result


def _normalize_score_events(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:500]:
        if not isinstance(item, dict):
            continue
        event: dict[str, Any] = {}
        for field in ("id", "source_id", "challenge_id", "correct_flag_count", "total_flag_count"):
            number = _integer(item.get(field))
            if number is not None:
                event[field] = number
        for field in (
            "challenge_code",
            "challenge_name",
            "event_time",
        ):
            text = _text(item.get(field), 300 if field == "challenge_name" else 160)
            if text is not None:
                event[field] = text
        delta = _number(item.get("score_delta"))
        if delta is not None:
            event["score_delta"] = delta
        cumulative = _number(item.get("cumulative_score"))
        if cumulative is not None:
            event["cumulative_score"] = cumulative
        output.append(event)
    return output


def _normalize_run_events(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:500]:
        if not isinstance(item, dict):
            continue
        event: dict[str, Any] = {}
        for field in ("id", "source_id", "challenge_id", "flag_index"):
            number = _integer(item.get(field))
            if number is not None:
                event[field] = number
        for field in (
            "operation_type",
            "challenge_code",
            "challenge_name",
            "event_time",
        ):
            text = _text(item.get(field), 300 if field == "challenge_name" else 160)
            if text is not None:
                event[field] = text
        awarded = _number(item.get("awarded"))
        if awarded is not None:
            event["awarded"] = awarded
        output.append(event)
    return output


def _normalize_timeline(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:500]:
        if not isinstance(item, dict):
            continue
        event: dict[str, Any] = {}
        for field in ("challenge_id",):
            number = _integer(item.get(field))
            if number is not None:
                event[field] = number
        for field in ("timestamp", "challenge_code", "challenge_name"):
            text = _text(item.get(field), 300 if field == "challenge_name" else 160)
            if text is not None:
                event[field] = text
        for field in ("challenge_score", "cumulative_score"):
            number = _number(item.get(field))
            if number is not None:
                event[field] = number
        output.append(event)
    return output


def _normalize_matrix(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:16]:
        if not isinstance(item, dict):
            continue
        dimension: dict[str, Any] = {}
        for field in ("key", "title"):
            text = _text(item.get(field), 80)
            if text is not None:
                dimension[field] = text
        for field in (
            "score",
            "challenge_total",
            "challenge_solved",
            "flag_total",
            "flag_correct",
        ):
            number = _number(item.get(field))
            if number is not None:
                dimension[field] = number
        challenges: list[dict[str, Any]] = []
        raw_challenges = item.get("challenges")
        if isinstance(raw_challenges, list):
            for challenge in raw_challenges[:150]:
                if not isinstance(challenge, dict):
                    continue
                clean: dict[str, Any] = {}
                for field in ("name", "difficulty", "state"):
                    text = _text(challenge.get(field), 200)
                    if text is not None:
                        clean[field] = text
                for field in (
                    "correct_flag_count",
                    "total_flag_count",
                    "total_score",
                    "level",
                ):
                    number = _number(challenge.get(field))
                    if number is not None:
                        clean[field] = number
                challenges.append(clean)
        dimension["challenges"] = challenges
        output.append(dimension)
    return output


def normalize_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 220_000:
        raise ValueError("payload is too large")
    forbidden = _contains_forbidden_key(payload)
    if forbidden:
        raise ValueError(f"forbidden field: {forbidden}")

    if event_type in {"run_status", "run_result", "run_created"}:
        return _normalize_base(payload)

    if event_type == "score_timeline":
        run_id = _integer(payload.get("run_id"))
        return {
            **({"run_id": run_id} if run_id is not None else {}),
            "items": _normalize_timeline(payload.get("items")),
        }

    if event_type == "active_run":
        active = payload.get("active_run")
        if active is None:
            return {"active_run": None}
        return {"active_run": _normalize_base(active) if isinstance(active, dict) else None}

    if event_type == "account_hint":
        result: dict[str, Any] = {}
        for field in ("username", "org", "agent_tag"):
            text = _text(payload.get(field), 160)
            if text:
                result[field] = text
        run_id = _integer(payload.get("run_id"))
        if run_id is not None:
            result["run_id"] = run_id
        return result

    if event_type == "heartbeat":
        result = _pick(payload, ("route", "status"))
        result = {key: _text(value, 160) for key, value in result.items() if _text(value, 160)}
        run_id = _integer(payload.get("run_id"))
        if run_id is not None:
            result["run_id"] = run_id
        score = _number(payload.get("current_score"))
        if score is not None:
            result["current_score"] = score
        return result

    raise ValueError("unsupported event type")
