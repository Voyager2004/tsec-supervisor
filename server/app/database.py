from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

from .ip_region import IpRegionResolver
from .schemas import IngestEnvelope
from .security import generate_token, token_hash


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _parse_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _score_value(row: dict[str, Any]) -> float:
    return _raw_score(row)


def _raw_score(row: dict[str, Any]) -> float:
    if row.get("final_score") is not None:
        return float(row["final_score"])
    if row.get("current_score") is not None:
        return float(row["current_score"])
    return 0.0


def _time_to_score_seconds(row: dict[str, Any]) -> int | None:
    reached_at = row.get("score_reached_at")
    started_at = row.get("started_at") or row.get("first_seen_at")
    if not reached_at or not started_at:
        return None
    try:
        reached = datetime.fromisoformat(str(reached_at)).astimezone(UTC)
        started = datetime.fromisoformat(str(started_at)).astimezone(UTC)
    except ValueError:
        return None
    return max(0, int((reached - started).total_seconds()))


def _timestamp_order(value: str | None) -> float:
    if not value:
        return float("inf")
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return float("inf")


# run 有效开始时间：started_at 缺失时退回首次上报时间（去掉微秒，保持 UTC）。
RUN_STARTED_EXPR = "julianday(COALESCE(runs.started_at, substr(runs.first_seen_at, 1, 19)))"


def _scoring_filter(
    scoring_start: str | None,
    scoring_end: str | None,
    scoring_set_name: str | None = None,
) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    if scoring_start is not None:
        clauses.append(f"{RUN_STARTED_EXPR} >= julianday(?)")
        params.append(scoring_start)
    if scoring_end is not None:
        clauses.append(f"{RUN_STARTED_EXPR} < julianday(?)")
        params.append(scoring_end)
    if scoring_set_name is not None:
        clauses.append("LOWER(TRIM(COALESCE(runs.set_name, ''))) = LOWER(TRIM(?))")
        params.append(scoring_set_name)
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


def _in_scoring_window(
    started_at: str | None, first_seen_at: str, scoring_start: str | None, scoring_end: str | None
) -> bool:
    if scoring_start is None and scoring_end is None:
        return True
    try:
        raw_start = started_at or first_seen_at
        started = datetime.fromisoformat(raw_start).astimezone(UTC)
    except ValueError:
        return False
    if scoring_start is not None:
        start = datetime.fromisoformat(scoring_start).astimezone(UTC)
        if started < start:
            return False
    if scoring_end is not None:
        end = datetime.fromisoformat(scoring_end).astimezone(UTC)
        return started < end
    return True


def _matches_scoring_set(set_name: str | None, scoring_set_name: str | None) -> bool:
    if scoring_set_name is None:
        return True
    return (set_name or "").strip().casefold() == scoring_set_name.strip().casefold()


class Database:
    def __init__(
        self, path: Path, ip_region: IpRegionResolver | None = None,
        expected_province: str | None = None,
    ) -> None:
        self.path = path
        self.ip_region = ip_region
        self.expected_province = expected_province
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = RLock()
        self._initialize()

    def _initialize(self) -> None:
        with self.lock, self.connection:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = NORMAL")
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1,
                    tsec_username TEXT,
                    org TEXT,
                    agent_tag TEXT,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT
                );

                CREATE TABLE IF NOT EXISTS installations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                    installation_id TEXT NOT NULL,
                    device_json TEXT NOT NULL DEFAULT '{}',
                    first_ip TEXT,
                    last_ip TEXT,
                    ip_hash TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(participant_id, installation_id)
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                    tsec_run_id INTEGER NOT NULL,
                    tsec_username TEXT,
                    org TEXT,
                    agent_tag TEXT,
                    set_id TEXT,
                    set_name TEXT,
                    status TEXT,
                    run_source TEXT,
                    evaluation_type TEXT,
                    current_score REAL,
                    max_score REAL,
                    final_score REAL,
                    score_percent REAL,
                    score_origin REAL,
                    accuracy REAL,
                    flag_correct INTEGER,
                    flag_total INTEGER,
                    challenge_solved INTEGER,
                    challenge_total INTEGER,
                    base_model_json TEXT,
                    radar_data_json TEXT,
                    matrix_json TEXT,
                    platform_created_at TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    duration_seconds INTEGER,
                    elapsed_seconds INTEGER,
                    score_elapsed_seconds INTEGER,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_heartbeat_at TEXT,
                    last_ip TEXT,
                    last_ip_hash TEXT,
                    last_installation_id TEXT,
                    verification_status TEXT NOT NULL DEFAULT 'observed',
                    risk_flags_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(participant_id, tsec_run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_tsec_run_id ON runs(tsec_run_id);
                CREATE INDEX IF NOT EXISTS idx_runs_last_seen_at ON runs(last_seen_at);

                CREATE TABLE IF NOT EXISTS score_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                    tsec_run_id INTEGER NOT NULL,
                    source_event_id TEXT NOT NULL,
                    challenge_id INTEGER,
                    challenge_code TEXT,
                    challenge_name TEXT,
                    score_delta REAL,
                    cumulative_score REAL,
                    flag_progress TEXT,
                    correct_flag_count INTEGER,
                    total_flag_count INTEGER,
                    score_note TEXT,
                    event_time TEXT,
                    received_at TEXT NOT NULL,
                    UNIQUE(participant_id, tsec_run_id, source_event_id)
                );

                CREATE TABLE IF NOT EXISTS score_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                    tsec_run_id INTEGER NOT NULL,
                    current_score REAL,
                    max_score REAL,
                    final_score REAL,
                    received_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_score_snapshots_run_time
                  ON score_snapshots(participant_id, tsec_run_id, received_at);

                CREATE TABLE IF NOT EXISTS activity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                    tsec_run_id INTEGER NOT NULL,
                    source_event_id TEXT NOT NULL,
                    operation_type TEXT,
                    challenge_id INTEGER,
                    challenge_code TEXT,
                    challenge_name TEXT,
                    awarded REAL,
                    flag_index INTEGER,
                    event_time TEXT,
                    received_at TEXT NOT NULL,
                    UNIQUE(participant_id, tsec_run_id, source_event_id)
                );

                CREATE TABLE IF NOT EXISTS ingest_audit (
                    event_id TEXT PRIMARY KEY,
                    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                    tsec_run_id INTEGER,
                    event_type TEXT NOT NULL,
                    installation_id TEXT NOT NULL,
                    ip_hash TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
                """
            )

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    def authenticate_participant(self, token: str) -> dict[str, Any] | None:
        digest = token_hash(token)
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM participants WHERE token_hash = ? AND active = 1", (digest,)
            ).fetchone()
        return dict(row) if row else None

    def create_participant(
        self, display_name: str, code: str | None = None, token: str | None = None
    ) -> dict[str, Any]:
        token = token or generate_token()
        code = code or f"P{datetime.now(UTC).strftime('%m%d')}-{generate_token('')[-6:].upper()}"
        now = utc_now()
        try:
            with self.lock, self.connection:
                cursor = self.connection.execute(
                    """
                    INSERT INTO participants(code, display_name, token_hash, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (code, display_name, token_hash(token), now),
                )
                participant_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            digest = token_hash(token)
            duplicate_token = self.connection.execute(
                "SELECT 1 FROM participants WHERE token_hash = ? LIMIT 1", (digest,)
            ).fetchone()
            message = "participant token already exists" if duplicate_token else (
                "participant code already exists"
            )
            raise ValueError(message) from exc
        return {
            "id": participant_id,
            "code": code,
            "display_name": display_name,
            "token": token,
            "created_at": now,
        }

    def create_participants_bulk(
        self, entries: list[dict[str, str]]
    ) -> dict[str, list[dict[str, Any]]]:
        now = utc_now()
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        with self.lock, self.connection:
            existing_codes = {
                row["code"] for row in self.connection.execute("SELECT code FROM participants")
            }
            for entry in entries:
                code = entry["code"]
                if code in existing_codes:
                    skipped.append({"display_name": entry["display_name"], "reason": "编号已存在"})
                    continue
                token = generate_token()
                cursor = self.connection.execute(
                    """
                    INSERT INTO participants(code, display_name, token_hash, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (code, entry["display_name"], token_hash(token), now),
                )
                existing_codes.add(code)
                created.append(
                    {
                        "id": cursor.lastrowid,
                        "code": code,
                        "display_name": entry["display_name"],
                        "token": token,
                    }
                )
        return {"created": created, "skipped": skipped}

    def rotate_participant_token(self, participant_id: int) -> str:
        token = generate_token()
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE participants SET token_hash = ? WHERE id = ?",
                (token_hash(token), participant_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("participant not found")
        return token

    def update_participant(
        self, participant_id: int, *, display_name: str | None, active: bool | None
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        if display_name is not None:
            assignments.append("display_name = ?")
            values.append(display_name)
        if active is not None:
            assignments.append("active = ?")
            values.append(1 if active else 0)
        if not assignments:
            return
        values.append(participant_id)
        with self.lock, self.connection:
            cursor = self.connection.execute(
                f"UPDATE participants SET {', '.join(assignments)} WHERE id = ?", values
            )
            if cursor.rowcount != 1:
                raise KeyError("participant not found")

    def delete_participant(self, participant_id: int) -> None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "DELETE FROM participants WHERE id = ?", (participant_id,)
            )
            if cursor.rowcount != 1:
                raise KeyError("participant not found")

    def _upsert_installation(
        self,
        participant_id: int,
        installation_id: str,
        device: dict[str, Any],
        raw_ip: str | None,
        ip_hash: str,
        now: str,
    ) -> bool:
        existing_row = self.connection.execute(
            """
            SELECT device_json FROM installations
            WHERE participant_id = ? AND installation_id = ?
            """,
            (participant_id, installation_id),
        ).fetchone()
        existing_device = _parse_json(existing_row["device_json"], {}) if existing_row else {}
        stable_fields = (
            "platform",
            "screen_bucket",
            "timezone",
            "locale",
            "hardware_concurrency",
            "device_memory_gb",
        )
        profile_changed = any(
            field in existing_device and field in device and existing_device[field] != device[field]
            for field in stable_fields
        )
        self.connection.execute(
            """
            INSERT INTO installations(
                participant_id, installation_id, device_json, first_ip, last_ip, ip_hash,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(participant_id, installation_id) DO UPDATE SET
                device_json = excluded.device_json,
                last_ip = excluded.last_ip,
                ip_hash = excluded.ip_hash,
                last_seen_at = excluded.last_seen_at
            """,
            (
                participant_id,
                installation_id,
                _json(device),
                raw_ip,
                raw_ip,
                ip_hash,
                now,
                now,
            ),
        )
        return profile_changed

    @staticmethod
    def _payload_run(payload: dict[str, Any], event_type: str) -> dict[str, Any] | None:
        if event_type == "active_run":
            active = payload.get("active_run")
            return active if isinstance(active, dict) else None
        return payload

    @staticmethod
    def _run_id(payload: dict[str, Any] | None) -> int | None:
        if not payload:
            return None
        value = payload.get("run_id")
        try:
            result = int(value)
        except (TypeError, ValueError):
            return None
        return result if result > 0 else None

    @staticmethod
    def _clock_skewed(observed_at: datetime) -> bool:
        current = datetime.now(UTC)
        observed = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=UTC)
        return abs((current - observed.astimezone(UTC)).total_seconds()) > 300

    def ingest(
        self,
        participant: dict[str, Any],
        envelope: IngestEnvelope,
        payload: dict[str, Any],
        *,
        raw_ip: str | None,
        ip_hash: str,
    ) -> dict[str, Any]:
        participant_id = int(participant["id"])
        now = utc_now()
        run_payload = self._payload_run(payload, envelope.event_type)
        run_id = self._run_id(run_payload)
        payload_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        installation_id = envelope.context.installation_id

        with self.lock, self.connection:
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO ingest_audit(
                    event_id, participant_id, tsec_run_id, event_type, installation_id,
                    ip_hash, payload_hash, observed_at, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.event_id,
                    participant_id,
                    run_id,
                    envelope.event_type,
                    installation_id,
                    ip_hash,
                    payload_hash,
                    envelope.observed_at.isoformat(),
                    now,
                ),
            )
            if cursor.rowcount == 0:
                return {"accepted": True, "duplicate": True, "run_id": run_id}

            risk_flags: set[str] = set()
            if self._clock_skewed(envelope.observed_at):
                risk_flags.add("clock_skew")
            if (self.ip_region and self.expected_province
                    and self.ip_region.is_outside_province(raw_ip, self.expected_province)):
                risk_flags.add("ip_region_outside")
            if self._upsert_installation(
                participant_id,
                installation_id,
                envelope.context.device.model_dump(exclude_none=True),
                raw_ip,
                ip_hash,
                now,
            ):
                risk_flags.add("device_profile_changed")

            account_source = run_payload or payload
            username = account_source.get("username") if isinstance(account_source, dict) else None
            org = account_source.get("org") if isinstance(account_source, dict) else None
            agent_tag = (
                account_source.get("agent_tag") if isinstance(account_source, dict) else None
            )
            if (
                participant.get("tsec_username")
                and username
                and participant["tsec_username"] != username
            ):
                risk_flags.add("account_changed")
            self.connection.execute(
                """
                UPDATE participants SET
                    last_seen_at = ?,
                    tsec_username = COALESCE(tsec_username, ?),
                    org = COALESCE(org, ?),
                    agent_tag = COALESCE(agent_tag, ?)
                WHERE id = ?
                """,
                (now, username, org, agent_tag, participant_id),
            )

            if run_id is not None:
                risk_flags.update(
                    self._upsert_run(
                        participant_id,
                        run_id,
                        run_payload or {},
                        event_type=envelope.event_type,
                        installation_id=installation_id,
                        raw_ip=raw_ip,
                        ip_hash=ip_hash,
                        now=now,
                        incoming_flags=risk_flags,
                    )
                )
                self._insert_score_events(participant_id, run_id, payload, now)
                self._insert_activity_events(participant_id, run_id, payload, now)
                self._insert_score_snapshot(
                    envelope.event_id, participant_id, run_id, run_payload or {}, now
                )

        return {
            "accepted": True,
            "duplicate": False,
            "run_id": run_id,
            "risk_flags": sorted(risk_flags),
            "received_at": now,
        }

    def _upsert_run(
        self,
        participant_id: int,
        run_id: int,
        payload: dict[str, Any],
        *,
        event_type: str,
        installation_id: str,
        raw_ip: str | None,
        ip_hash: str,
        now: str,
        incoming_flags: set[str],
    ) -> set[str]:
        existing_row = self.connection.execute(
            "SELECT * FROM runs WHERE participant_id = ? AND tsec_run_id = ?",
            (participant_id, run_id),
        ).fetchone()
        existing = dict(existing_row) if existing_row else None
        verification_locked = bool(
            existing and existing.get("verification_status") == "verified"
        )
        flags = set(incoming_flags)
        if existing:
            flags.update(_parse_json(existing.get("risk_flags_json"), []))
            if existing.get("last_ip_hash") and existing["last_ip_hash"] != ip_hash:
                flags.add("ip_changed")
            if (
                existing.get("last_installation_id")
                and existing["last_installation_id"] != installation_id
            ):
                flags.add("device_changed")
            if (
                existing.get("current_score") is not None
                and payload.get("current_score") is not None
                and float(payload["current_score"]) < float(existing["current_score"])
            ):
                flags.add("score_regressed")
            if (
                existing.get("max_score") is not None
                and payload.get("max_score") is not None
                and float(payload["max_score"]) != float(existing["max_score"])
            ):
                flags.add("max_score_changed")
            if (
                existing.get("tsec_username")
                and payload.get("username")
                and existing["tsec_username"] != payload["username"]
            ):
                flags.add("account_changed")

        shared_rows = self.connection.execute(
            "SELECT participant_id FROM runs WHERE tsec_run_id = ? AND participant_id <> ?",
            (run_id, participant_id),
        ).fetchall()
        if shared_rows:
            flags.add("run_id_shared")
            for row in shared_rows:
                other = self.connection.execute(
                    "SELECT risk_flags_json FROM runs WHERE participant_id = ? AND tsec_run_id = ?",
                    (row["participant_id"], run_id),
                ).fetchone()
                other_flags = set(_parse_json(other["risk_flags_json"] if other else None, []))
                other_flags.add("run_id_shared")
                self.connection.execute(
                    """
                    UPDATE runs SET
                        risk_flags_json = CASE
                            WHEN verification_status = 'verified' THEN '[]'
                            ELSE ?
                        END,
                        verification_status = CASE
                            WHEN verification_status = 'verified' THEN 'verified'
                            ELSE 'risk'
                        END
                    WHERE participant_id = ? AND tsec_run_id = ?
                    """,
                    (_json(sorted(other_flags)), row["participant_id"], run_id),
                )

        base_model = payload.get("base_model") or None
        radar_data = payload.get("radar_data") or None
        matrix = payload.get("matrix") or None
        heartbeat_at = now if event_type == "heartbeat" else None
        stored_flags = set() if verification_locked else flags
        verification_status = "verified" if verification_locked else (
            "risk" if stored_flags else (existing or {}).get("verification_status", "observed")
        )

        values = {
            "participant_id": participant_id,
            "tsec_run_id": run_id,
            "tsec_username": payload.get("username"),
            "org": payload.get("org"),
            "agent_tag": payload.get("agent_tag"),
            "set_id": payload.get("set_id"),
            "set_name": payload.get("set_name"),
            "status": None if event_type == "heartbeat" else payload.get("status"),
            "run_source": payload.get("run_source"),
            "evaluation_type": payload.get("evaluation_type"),
            "current_score": payload.get("current_score"),
            "max_score": payload.get("max_score"),
            "final_score": payload.get("final_score"),
            "score_percent": payload.get("score_percent"),
            "score_origin": payload.get("score_origin"),
            "accuracy": payload.get("accuracy"),
            "flag_correct": payload.get("flag_correct") or payload.get("final_flags"),
            "flag_total": payload.get("flag_total"),
            "challenge_solved": payload.get("challenge_solved"),
            "challenge_total": payload.get("challenge_total"),
            "base_model_json": _json(base_model) if base_model else None,
            "radar_data_json": _json(radar_data) if radar_data else None,
            "matrix_json": _json(matrix) if matrix else None,
            "platform_created_at": payload.get("created_at"),
            "started_at": payload.get("started_at"),
            "ended_at": payload.get("ended_at"),
            "duration_seconds": payload.get("duration_seconds"),
            "elapsed_seconds": payload.get("elapsed_seconds"),
            "score_elapsed_seconds": payload.get("score_elapsed_seconds"),
            "first_seen_at": (existing or {}).get("first_seen_at", now),
            "last_seen_at": now,
            "last_heartbeat_at": heartbeat_at,
            "last_ip": raw_ip,
            "last_ip_hash": ip_hash,
            "last_installation_id": installation_id,
            "verification_status": verification_status,
            "risk_flags_json": _json(sorted(stored_flags)),
        }
        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        update_columns = [
            column
            for column in columns
            if column not in {"participant_id", "tsec_run_id", "first_seen_at"}
        ]
        updates = ",\n".join(
            (
                f"{column} = COALESCE(runs.{column}, excluded.{column})"
                if column == "started_at"
                else f"{column} = COALESCE(excluded.{column}, runs.{column})"
            )
            for column in update_columns
        )
        self.connection.execute(
            f"""
            INSERT INTO runs({", ".join(columns)}) VALUES ({placeholders})
            ON CONFLICT(participant_id, tsec_run_id) DO UPDATE SET
                {updates}
            """,
            [values[column] for column in columns],
        )
        return flags

    def _insert_score_events(
        self, participant_id: int, run_id: int, payload: dict[str, Any], now: str
    ) -> None:
        events: list[dict[str, Any]] = []
        raw_score_events = payload.get("score_events")
        if isinstance(raw_score_events, list):
            events.extend(raw_score_events)
        timeline = payload.get("items")
        if isinstance(timeline, list):
            for item in timeline:
                events.append(
                    {
                        "id": None,
                        "challenge_id": item.get("challenge_id"),
                        "challenge_code": item.get("challenge_code"),
                        "challenge_name": item.get("challenge_name"),
                        "score_delta": item.get("challenge_score"),
                        "cumulative_score": item.get("cumulative_score"),
                        "score_note": item.get("score_note"),
                        "event_time": item.get("timestamp"),
                    }
                )

        for event in events:
            source = event.get("id") or event.get("source_id")
            if source is None:
                source = hashlib.sha256(_json(event).encode("utf-8")).hexdigest()[:24]
            source_event_id = f"score:{source}"
            self.connection.execute(
                """
                INSERT OR IGNORE INTO score_events(
                    participant_id, tsec_run_id, source_event_id, challenge_id,
                    challenge_code, challenge_name, score_delta, cumulative_score,
                    flag_progress, correct_flag_count, total_flag_count, score_note,
                    event_time, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    participant_id,
                    run_id,
                    source_event_id,
                    event.get("challenge_id"),
                    event.get("challenge_code"),
                    event.get("challenge_name"),
                    event.get("score_delta"),
                    event.get("cumulative_score"),
                    event.get("flag_progress"),
                    event.get("correct_flag_count"),
                    event.get("total_flag_count"),
                    event.get("score_note"),
                    event.get("event_time"),
                    now,
                ),
            )

    def _insert_activity_events(
        self, participant_id: int, run_id: int, payload: dict[str, Any], now: str
    ) -> None:
        events = payload.get("run_events")
        if not isinstance(events, list):
            return
        for event in events:
            source = event.get("id") or event.get("source_id")
            if source is None:
                source = hashlib.sha256(_json(event).encode("utf-8")).hexdigest()[:24]
            self.connection.execute(
                """
                INSERT OR IGNORE INTO activity_events(
                    participant_id, tsec_run_id, source_event_id, operation_type,
                    challenge_id, challenge_code, challenge_name, awarded, flag_index,
                    event_time, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    participant_id,
                    run_id,
                    f"activity:{source}",
                    event.get("operation_type"),
                    event.get("challenge_id"),
                    event.get("challenge_code"),
                    event.get("challenge_name"),
                    event.get("awarded"),
                    event.get("flag_index"),
                    event.get("event_time"),
                    now,
                ),
            )

    def _insert_score_snapshot(
        self,
        event_id: str,
        participant_id: int,
        run_id: int,
        payload: dict[str, Any],
        now: str,
    ) -> None:
        current_score = payload.get("current_score")
        max_score = payload.get("max_score")
        final_score = payload.get("final_score")
        if current_score is None and final_score is None:
            return
        self.connection.execute(
            """
            INSERT OR IGNORE INTO score_snapshots(
                event_id, participant_id, tsec_run_id, current_score,
                max_score, final_score, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                participant_id,
                run_id,
                current_score,
                max_score,
                final_score,
                now,
            ),
        )

    def scoreboard(
        self,
        scoring_start: str | None = None,
        scoring_end: str | None = None,
        scoring_set_name: str | None = None,
    ) -> list[dict[str, Any]]:
        scoring_sql, scoring_params = _scoring_filter(
            scoring_start, scoring_end, scoring_set_name
        )
        snapshot_cutoff_sql = ""
        reached_event_cutoff_sql = ""
        reached_cutoff_sql = ""
        event_cutoff_sql = ""
        query_params = list(scoring_params)
        if scoring_end is not None:
            snapshot_cutoff_sql = "WHERE julianday(ss.received_at) <= julianday(?)"
            reached_event_cutoff_sql = "AND julianday(se2.received_at) <= julianday(?)"
            reached_cutoff_sql = "AND julianday(ss.received_at) <= julianday(?)"
            event_cutoff_sql = "AND julianday(se.received_at) <= julianday(?)"
            query_params.extend((scoring_end, scoring_end, scoring_end, scoring_end))
        with self.lock:
            rows = self.connection.execute(
                f"""
                WITH latest AS (
                    SELECT runs.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY participant_id
                               ORDER BY first_seen_at DESC, id DESC
                           ) AS latest_rank
                    FROM runs
                    WHERE 1=1{scoring_sql}
                ), cutoff_snapshots AS (
                    SELECT ss.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY ss.participant_id, ss.tsec_run_id
                               ORDER BY ss.received_at DESC, ss.id DESC
                           ) AS cutoff_rank
                    FROM score_snapshots AS ss
                    {snapshot_cutoff_sql}
                )
                SELECT latest.*, participants.code, participants.display_name,
                       participants.tsec_username AS participant_username,
                       participants.agent_tag AS participant_agent_tag,
                       cutoff.current_score AS cutoff_current_score,
                       cutoff.max_score AS cutoff_max_score,
                       cutoff.final_score AS cutoff_final_score,
                       COALESCE(
                         (SELECT se2.event_time
                          FROM score_events AS se2
                          WHERE se2.participant_id = latest.participant_id
                            AND se2.tsec_run_id = latest.tsec_run_id
                            AND se2.event_time IS NOT NULL
                            AND se2.cumulative_score >=
                                COALESCE(cutoff.final_score, cutoff.current_score, 0)
                            {reached_event_cutoff_sql}
                          ORDER BY julianday(se2.event_time), se2.id
                          LIMIT 1),
                         (SELECT MIN(ss.received_at)
                          FROM score_snapshots AS ss
                          WHERE ss.participant_id = latest.participant_id
                            AND ss.tsec_run_id = latest.tsec_run_id
                            AND COALESCE(ss.final_score, ss.current_score, 0)
                                >= COALESCE(cutoff.final_score, cutoff.current_score, 0)
                            {reached_cutoff_sql})
                       ) AS score_reached_at,
                       (SELECT COUNT(DISTINCT COALESCE(se.challenge_code,
                                CAST(se.challenge_id AS TEXT)))
                        FROM score_events AS se
                        WHERE se.participant_id = latest.participant_id
                          AND se.tsec_run_id = latest.tsec_run_id
                          AND (COALESCE(se.score_delta, 0) > 0
                               OR COALESCE(se.correct_flag_count, 0) >= 1)
                          {event_cutoff_sql}) AS solved
                FROM latest
                JOIN participants ON participants.id = latest.participant_id
                LEFT JOIN cutoff_snapshots AS cutoff
                  ON cutoff.participant_id = latest.participant_id
                 AND cutoff.tsec_run_id = latest.tsec_run_id
                 AND cutoff.cutoff_rank = 1
                WHERE latest.latest_rank = 1 AND participants.active = 1
                """,
                query_params,
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["current_score"] = item.pop("cutoff_current_score")
            item["final_score"] = item.pop("cutoff_final_score")
            cutoff_max_score = item.pop("cutoff_max_score")
            if cutoff_max_score is not None:
                item["max_score"] = cutoff_max_score
            item["display_score"] = _score_value(item)
            item["time_to_score_seconds"] = _time_to_score_seconds(item)
            items.append(
                {
                    "participant_code": item["code"],
                    "display_name": item["display_name"],
                    "tsec_username": item.get("participant_username") or item.get("tsec_username"),
                    "agent_tag": item.get("participant_agent_tag") or item.get("agent_tag"),
                    "run_id": item["tsec_run_id"],
                    "set_name": item.get("set_name"),
                    "status": item.get("status") or "observed",
                    "current_score": item.get("current_score"),
                    "max_score": item.get("max_score"),
                    "final_score": item.get("final_score"),
                    "display_score": item["display_score"],
                    "started_at": item.get("started_at"),
                    "solved": item.get("solved") or 0,
                    "challenge_total": item.get("challenge_total"),
                    "flag_correct": item.get("flag_correct"),
                    "flag_total": item.get("flag_total"),
                    "elapsed_seconds": item.get("time_to_score_seconds")
                    if item.get("time_to_score_seconds") is not None
                    else (item.get("score_elapsed_seconds") or item.get("elapsed_seconds")),
                    "score_reached_at": item.get("score_reached_at"),
                    "verification_status": item["verification_status"],
                    "risk_count": len(_parse_json(item.get("risk_flags_json"), [])),
                    "last_seen_at": item["last_seen_at"],
                }
            )
        items.sort(
            key=lambda item: (
                -float(item.get("display_score") or 0),
                int(item["elapsed_seconds"])
                if item.get("elapsed_seconds") is not None
                else 10**12,
                _timestamp_order(item.get("score_reached_at")),
                item["display_name"],
            )
        )
        for index, item in enumerate(items, 1):
            item["rank"] = index
        return items

    def admin_state(
        self,
        scoring_start: str | None = None,
        scoring_end: str | None = None,
        scoring_set_name: str | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            participants = [
                dict(row)
                for row in self.connection.execute(
                    """
                    SELECT id, code, display_name, active, tsec_username, org, agent_tag,
                           created_at, last_seen_at
                    FROM participants ORDER BY id
                    """
                ).fetchall()
            ]
            rows = self.connection.execute(
                """
                SELECT runs.*, participants.code AS participant_code,
                       participants.display_name,
                       participants.tsec_username AS bound_tsec_username,
                       participants.org AS bound_org,
                       participants.agent_tag AS bound_agent_tag,
                       installations.device_json
                FROM runs
                JOIN participants ON participants.id = runs.participant_id
                LEFT JOIN installations
                  ON installations.participant_id = runs.participant_id
                 AND installations.installation_id = runs.last_installation_id
                ORDER BY runs.last_seen_at DESC
                """
            ).fetchall()
            counts = dict(
                self.connection.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM participants WHERE active = 1) AS participants,
                      (SELECT COUNT(*) FROM runs) AS runs,
                      (SELECT COUNT(*) FROM ingest_audit) AS events,
                      (SELECT COUNT(*) FROM score_events) AS score_events
                    """
                ).fetchone()
            )
        runs: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["display_score"] = _score_value(item)
            item["scoring"] = _in_scoring_window(
                item.get("started_at"), item["first_seen_at"], scoring_start, scoring_end
            ) and _matches_scoring_set(item.get("set_name"), scoring_set_name)
            item["ip_location"] = (
                self.ip_region.location_text(item.get("last_ip"))
                if self.ip_region is not None
                else None
            )
            item["risk_flags"] = _parse_json(item.pop("risk_flags_json"), [])
            item["base_model"] = _parse_json(item.pop("base_model_json"), [])
            item["radar_data"] = _parse_json(item.pop("radar_data_json"), {})
            item["matrix"] = _parse_json(item.pop("matrix_json"), [])
            item["device"] = _parse_json(item.pop("device_json"), {})
            item.pop("last_ip_hash", None)
            runs.append(item)
        return {"counts": counts, "participants": participants, "runs": runs}

    def score_series(
        self,
        scoring_start: str | None = None,
        scoring_end: str | None = None,
        scoring_set_name: str | None = None,
    ) -> list[dict[str, Any]]:
        scoring_sql, scoring_params = _scoring_filter(
            scoring_start, scoring_end, scoring_set_name
        )
        snapshot_cutoff_sql = ""
        query_params = list(scoring_params)
        if scoring_end is not None:
            snapshot_cutoff_sql = (
                "AND julianday(snapshots.received_at) <= julianday(?)"
            )
            query_params.append(scoring_end)
        with self.lock:
            rows = self.connection.execute(
                f"""
                WITH latest_runs AS (
                    SELECT runs.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY participant_id
                               ORDER BY first_seen_at DESC, id DESC
                           ) AS latest_rank
                    FROM runs
                    WHERE 1=1{scoring_sql}
                )
                SELECT snapshots.participant_id, snapshots.tsec_run_id,
                       snapshots.current_score, snapshots.max_score,
                       snapshots.final_score, snapshots.received_at,
                       participants.code, participants.display_name,
                       participants.tsec_username
                FROM score_snapshots AS snapshots
                JOIN latest_runs
                  ON latest_runs.participant_id = snapshots.participant_id
                 AND latest_runs.tsec_run_id = snapshots.tsec_run_id
                 AND latest_runs.latest_rank = 1
                JOIN participants ON participants.id = snapshots.participant_id
                WHERE participants.active = 1
                  {snapshot_cutoff_sql}
                ORDER BY snapshots.participant_id, snapshots.received_at, snapshots.id
                """,
                query_params,
            ).fetchall()

        grouped: dict[int, dict[str, Any]] = {}
        for raw in rows:
            row = dict(raw)
            participant_id = int(row["participant_id"])
            series = grouped.setdefault(
                participant_id,
                {
                    "participant_code": row["code"],
                    "display_name": row["display_name"],
                    "tsec_username": row.get("tsec_username"),
                    "run_id": row["tsec_run_id"],
                    "points": [],
                },
            )
            raw_score = _raw_score(row)
            points = series["points"]
            point = {"timestamp": row["received_at"], "score": raw_score}
            if points and points[-1]["score"] == raw_score:
                points[-1] = point
            else:
                points.append(point)

        output: list[dict[str, Any]] = []
        for series in grouped.values():
            points = series["points"]
            if len(points) > 300:
                step = (len(points) - 1) / 299
                indices = sorted({round(index * step) for index in range(300)})
                series["points"] = [points[index] for index in indices]
            output.append(series)
        output.sort(key=lambda item: (item["display_name"], item["participant_code"]))
        return output

    def challenge_stats(
        self,
        scoring_start: str | None = None,
        scoring_end: str | None = None,
        scoring_set_name: str | None = None,
    ) -> dict[str, Any]:
        scoring_sql, scoring_params = _scoring_filter(
            scoring_start, scoring_end, scoring_set_name
        )
        event_cutoff_sql = ""
        query_params = list(scoring_params)
        if scoring_end is not None:
            event_cutoff_sql = "AND julianday(se.received_at) <= julianday(?)"
            query_params.append(scoring_end)
        with self.lock:
            rows = self.connection.execute(
                f"""
                WITH latest AS (
                    SELECT runs.participant_id, runs.tsec_run_id, runs.set_id, runs.set_name,
                           ROW_NUMBER() OVER (
                               PARTITION BY runs.participant_id
                               ORDER BY runs.first_seen_at DESC, runs.id DESC
                           ) AS latest_rank
                    FROM runs
                    JOIN participants ON participants.id = runs.participant_id
                    WHERE participants.active = 1{scoring_sql}
                )
                SELECT l.set_id, l.set_name, l.participant_id,
                       COALESCE(se.challenge_code, CAST(se.challenge_id AS TEXT)) AS challenge_key,
                       se.score_delta, se.correct_flag_count
                FROM latest AS l
                LEFT JOIN score_events AS se
                  ON se.participant_id = l.participant_id
                 AND se.tsec_run_id = l.tsec_run_id
                 {event_cutoff_sql}
                WHERE l.latest_rank = 1
                """,
                query_params,
            ).fetchall()

        set_runners: dict[str | None, set[int]] = {}
        set_names: dict[str | None, str | None] = {}
        for row in rows:
            set_key = row["set_id"]
            set_runners.setdefault(set_key, set()).add(int(row["participant_id"]))
            if set_key not in set_names or (row["set_name"] and not set_names.get(set_key)):
                set_names[set_key] = row["set_name"]

        primary_set_id: str | None = None
        primary_runners = -1
        for set_key, runners in set_runners.items():
            if len(runners) > primary_runners or (
                len(runners) == primary_runners and set_key is not None
                and primary_set_id is None
            ):
                primary_set_id = set_key
                primary_runners = len(runners)

        solved_challenges: set[str] = set()
        solve_count: set[tuple[int, str]] = set()
        if primary_set_id is not None:
            for row in rows:
                if row["set_id"] != primary_set_id:
                    continue
                challenge_key = row["challenge_key"]
                if not challenge_key:
                    continue
                delta = row["score_delta"] or 0
                flags = row["correct_flag_count"] or 0
                if delta <= 0 and flags < 1:
                    continue
                solved_challenges.add(challenge_key)
                solve_count.add((int(row["participant_id"]), challenge_key))

        sets = [
            {
                "set_id": set_key,
                "set_name": set_names.get(set_key),
                "runners": len(runners),
            }
            for set_key, runners in sorted(
                set_runners.items(), key=lambda item: (-len(item[1]), str(item[0]))
            )
        ]
        return {
            "sets": sets,
            "primary_set_id": primary_set_id,
            "primary_set_name": set_names.get(primary_set_id),
            "challenges_solved": len(solved_challenges),
            "solve_count": len(solve_count),
        }

    def update_verification(self, run_id: int, status: str) -> None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """UPDATE runs SET
                       verification_status = ?,
                       risk_flags_json = CASE WHEN ? = 'verified' THEN '[]'
                                              ELSE risk_flags_json END
                   WHERE id = ?""",
                (status, status, run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("run not found")

    def prune(self, retention_days: int) -> dict[str, int]:
        cutoff = (datetime.now(UTC) - timedelta(days=max(1, retention_days))).isoformat()
        deleted: dict[str, int] = {}
        with self.lock, self.connection:
            for table, timestamp_column in (
                ("score_events", "received_at"),
                ("score_snapshots", "received_at"),
                ("activity_events", "received_at"),
                ("ingest_audit", "received_at"),
                ("runs", "last_seen_at"),
                ("installations", "last_seen_at"),
            ):
                cursor = self.connection.execute(
                    f"DELETE FROM {table} WHERE {timestamp_column} < ?", (cutoff,)
                )
                deleted[table] = cursor.rowcount
        return deleted

    def recent_score_events(self, participant_id: int, tsec_run_id: int) -> list[dict[str, Any]]:
        with self.lock:
            return [
                dict(row)
                for row in self.connection.execute(
                    """
                    SELECT challenge_code, challenge_name, score_delta, cumulative_score,
                           flag_progress, score_note, event_time, received_at
                    FROM score_events
                    WHERE participant_id = ? AND tsec_run_id = ?
                    ORDER BY COALESCE(event_time, received_at) DESC
                    LIMIT 200
                    """,
                    (participant_id, tsec_run_id),
                ).fetchall()
            ]
