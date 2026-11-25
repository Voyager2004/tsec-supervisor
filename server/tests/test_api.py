from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.main import create_app

ADMIN_TOKEN = "admin-" + "a" * 40


@pytest.fixture
def client(tmp_path: Path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, database=Database(settings.db_path))
    with TestClient(app) as test_client:
        yield test_client


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        environment="test",
        db_path=tmp_path / "test.sqlite3",
        event_name="Test Agent Cup",
        admin_token=ADMIN_TOKEN,
        ip_hash_salt="salt-" + "b" * 40,
        public_base_url="http://testserver",
        trusted_proxies=frozenset({"testclient"}),
        store_raw_ip=True,
        retention_days=30,
        cors_origin_regex=r"^chrome-extension://[a-p]{32}$",
        scoring_start=None,
        scoring_end=None,
        scoring_set_name=None,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def windowed_client(tmp_path: Path):
    settings = make_settings(
        tmp_path,
        scoring_start="2030-01-01T09:00:00+08:00",
        scoring_end="2030-01-01T11:00:00+08:00",
    )
    app = create_app(settings=settings, database=Database(settings.db_path))
    with TestClient(app) as test_client:
        yield test_client


def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def make_envelope(
    payload: dict[str, object],
    *,
    event_type: str = "run_status",
    event_id: str | None = None,
    installation_id: str = "install-test-0001",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": event_id or f"evt-{uuid4()}",
        "event_type": event_type,
        "observed_at": datetime.now(UTC).isoformat(),
        "context": {
            "installation_id": installation_id,
            "extension_version": "0.1.0",
            "page_path": "/benchmark/running/9001",
            "page_visibility": "visible",
            "device": {
                "platform": "macOS",
                "browser": "Chrome",
                "chrome_major": 140,
                "screen_bucket": "large",
                "timezone": "Asia/Shanghai",
                "locale": "zh-CN",
                "hardware_concurrency": 8,
                "device_memory_gb": 8,
            },
        },
        "payload": payload,
    }


def create_participant(client: TestClient, name: str, code: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/admin/participants",
        headers=admin_headers(),
        json={"display_name": name, "code": code},
    )
    assert response.status_code == 201
    return response.json()


def ingest(
    client: TestClient,
    token: str,
    envelope: dict[str, object],
    *,
    ip: str = "203.0.113.10",
):
    return client.post(
        "/api/v1/ingest",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Forwarded-For": ip,
        },
        json=envelope,
    )


def test_health_public_config_and_security_headers(client: TestClient):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in health.headers["content-security-policy"]

    config = client.get("/api/v1/public/config")
    assert config.json()["event_name"] == "Test Agent Cup"
    assert "trust_notice" not in config.json()

    homepage = client.get("/")
    assert homepage.status_code == 200
    assert "本地模式实时观察成绩" not in homepage.text
    assert "公开大屏仅展示比赛所需信息" not in homepage.text
    assert 'id="score-chart"' in homepage.text
    assert 'id="score-distribution-chart"' in homepage.text
    assert 'id="chart-mode-distribution"' in homepage.text
    assert 'id="admin-login-close" class="icon-button" type="button"' in homepage.text
    assert 'formmethod="dialog"' not in homepage.text


def test_admin_auth_and_participant_token_is_one_time_only(client: TestClient):
    assert client.get("/api/v1/admin/state").status_code == 401
    wrong = client.get("/api/v1/admin/state", headers={"Authorization": "Bearer definitely-wrong"})
    assert wrong.status_code == 403

    created = create_participant(client, "Alice", "ALICE")
    assert str(created["token"]).startswith("tsp_")

    session = client.get(
        "/api/v1/participant/session",
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert session.status_code == 200
    assert session.json()["display_name"] == "Alice"

    state = client.get("/api/v1/admin/state", headers=admin_headers()).json()
    assert state["participants"][0]["code"] == "ALICE"
    assert "token" not in state["participants"][0]
    assert "token_hash" not in state["participants"][0]


def test_ingest_scoreboard_idempotency_and_sensitive_field_rejection(client: TestClient):
    participant = create_participant(client, "Alice", "ALICE")
    token = str(participant["token"])
    event_id = f"evt-{uuid4()}"
    envelope = make_envelope(
        {
            "run_id": 9001,
            "status": "running",
            "username": "alice_tsec",
            "agent_tag": "alpha-agent",
            "set_name": "Agent Benchmark",
            "current_score": 60,
            "max_score": 100,
            "elapsed_seconds": 123,
            "score_events": [
                {
                    "id": 71,
                    "challenge_code": "web-1",
                    "challenge_name": "Web One",
                    "score_delta": 20,
                    "event_time": "2026-09-18T08:00:00Z",
                }
            ],
            "run_events": [
                {
                    "id": 99,
                    "operation_type": "submit",
                    "challenge_code": "web-1",
                    "awarded": 20,
                }
            ],
        },
        event_id=event_id,
    )
    first = ingest(client, token, envelope)
    assert first.status_code == 202
    assert first.json()["duplicate"] is False

    duplicate = ingest(client, token, envelope)
    assert duplicate.status_code == 202
    assert duplicate.json()["duplicate"] is True

    board = client.get("/api/v1/public/scoreboard").json()["items"]
    assert board[0]["display_name"] == "Alice"
    assert board[0]["display_score"] == 60
    assert board[0]["tsec_username"] == "alice_tsec"

    progressed = make_envelope(
        {
            "run_id": 9001,
            "status": "running",
            "username": "alice_tsec",
            "current_score": 85,
            "max_score": 100,
        }
    )
    assert ingest(client, token, progressed).status_code == 202
    score_series = client.get("/api/v1/public/score-series").json()["series"]
    assert len(score_series) == 1
    assert score_series[0]["participant_code"] == "ALICE"
    assert score_series[0]["tsec_username"] == "alice_tsec"
    assert [point["score"] for point in score_series[0]["points"]] == [60, 85]

    state = client.get("/api/v1/admin/state", headers=admin_headers()).json()
    assert state["counts"] == {
        "participants": 1,
        "runs": 1,
        "events": 2,
        "score_events": 1,
    }

    forbidden = make_envelope({"run_id": 9001, "benchmark_token": "must-not-leave-browser"})
    rejected = ingest(client, token, forbidden)
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "forbidden field: benchmark_token"

    raw_flag = make_envelope({"run_id": 9001, "submitted_flag": "flag{never-store-this}"})
    rejected = ingest(client, token, raw_flag)
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "forbidden field: submitted_flag"


def test_risk_flags_for_account_ip_device_score_and_shared_run(client: TestClient):
    alice = create_participant(client, "Alice", "ALICE")
    bob = create_participant(client, "Bob", "BOB")

    original = make_envelope(
        {
            "run_id": 777,
            "status": "running",
            "username": "alice",
            "current_score": 80,
            "max_score": 100,
        }
    )
    assert ingest(client, str(alice["token"]), original).status_code == 202

    changed = make_envelope(
        {
            "run_id": 777,
            "status": "running",
            "username": "third-account",
            "current_score": 70,
            "max_score": 120,
        },
        installation_id="install-test-0002",
    )
    response = ingest(client, str(alice["token"]), changed, ip="198.51.100.22")
    assert response.status_code == 202
    assert set(response.json()["risk_flags"]) >= {
        "account_changed",
        "device_changed",
        "ip_changed",
        "max_score_changed",
        "score_regressed",
    }

    changed_profile = make_envelope(
        {
            "run_id": 778,
            "status": "running",
            "username": "not-alice",
            "current_score": 5,
            "max_score": 100,
        },
        installation_id="install-test-0002",
    )
    changed_profile["context"]["device"]["platform"] = "Windows"
    response = ingest(client, str(alice["token"]), changed_profile, ip="198.51.100.22")
    assert response.status_code == 202
    assert "device_profile_changed" in response.json()["risk_flags"]
    assert "account_changed" in response.json()["risk_flags"]

    shared = make_envelope(
        {"run_id": 777, "status": "running", "username": "bob", "current_score": 10}
    )
    response = ingest(client, str(bob["token"]), shared, ip="192.0.2.30")
    assert response.status_code == 202
    assert "run_id_shared" in response.json()["risk_flags"]

    state = client.get("/api/v1/admin/state", headers=admin_headers()).json()
    alice_participant = next(
        participant for participant in state["participants"] if participant["code"] == "ALICE"
    )
    assert alice_participant["tsec_username"] == "alice"
    assert all(
        run["bound_tsec_username"] == "alice"
        for run in state["runs"]
        if run["participant_code"] == "ALICE"
    )
    runs = {run["participant_code"]: run for run in state["runs"]}
    assert runs["ALICE"]["verification_status"] == "risk"
    assert "run_id_shared" in runs["ALICE"]["risk_flags"]
    assert runs["BOB"]["verification_status"] == "risk"


def test_score_series_returns_raw_scores(client: TestClient):
    participant = create_participant(client, "Alice", "ALICE")
    token = str(participant["token"])
    first = make_envelope(
        {
            "run_id": 9001,
            "status": "running",
            "username": "alice",
            "current_score": 800,
            "max_score": 28300,
        }
    )
    assert ingest(client, token, first).status_code == 202
    second = make_envelope(
        {
            "run_id": 9001,
            "status": "running",
            "username": "alice",
            "current_score": 1200,
            "max_score": 28300,
        }
    )
    assert ingest(client, token, second).status_code == 202

    series = client.get("/api/v1/public/score-series").json()["series"]
    assert [point["score"] for point in series[0]["points"]] == [800.0, 1200.0]

    board = client.get("/api/v1/public/scoreboard").json()["items"]
    assert board[0]["display_score"] == 1200


def test_equal_scores_rank_by_shorter_time_to_reach_score(client: TestClient):
    alice = create_participant(client, "Alice", "ALICE")
    bob = create_participant(client, "Bob", "BOB")
    started_at = "2026-09-18T07:00:00Z"

    assert (
        ingest(
            client,
            str(alice["token"]),
            make_envelope({"run_id": 1001, "started_at": started_at, "current_score": 50}),
        ).status_code
        == 202
    )
    assert (
        ingest(
            client,
            str(bob["token"]),
            make_envelope({"run_id": 1002, "started_at": started_at, "current_score": 100}),
        ).status_code
        == 202
    )
    assert (
        ingest(
            client,
            str(alice["token"]),
            make_envelope({"run_id": 1001, "started_at": started_at, "current_score": 100}),
        ).status_code
        == 202
    )

    board = client.get("/api/v1/public/scoreboard").json()["items"]
    assert [(item["display_name"], item["display_score"]) for item in board] == [
        ("Bob", 100.0),
        ("Alice", 100.0),
    ]
    assert board[0]["score_reached_at"] < board[1]["score_reached_at"]


def test_public_scores_freeze_at_scoring_end(tmp_path: Path):
    settings = make_settings(
        tmp_path,
        scoring_start="2026-09-19T09:00:00+08:00",
        scoring_end="2026-09-19T11:00:00+08:00",
        scoring_set_name="XBOW Validation Benchmarks",
    )
    app = create_app(settings=settings, database=Database(settings.db_path))
    with TestClient(app) as client:
        participant = create_participant(client, "Alice", "ALICE")
        common = {
            "run_id": 2001,
            "started_at": "2026-09-19T09:10:00+08:00",
            "set_id": "xbow",
            "set_name": "XBOW Validation Benchmarks",
            "max_score": 28300,
        }
        before = {
            **common,
            "current_score": 100,
            "score_events": [
                {
                    "id": 1,
                    "challenge_code": "web-1",
                    "score_delta": 100,
                    "cumulative_score": 100,
                    "event_time": "2026-09-19T10:58:00+08:00",
                }
            ],
        }
        after = {
            **common,
            "current_score": 200,
            "score_events": [
                {
                    "id": 2,
                    "challenge_code": "web-2",
                    "score_delta": 100,
                    "cumulative_score": 200,
                    "event_time": "2026-09-19T11:00:30+08:00",
                }
            ],
        }
        assert ingest(client, str(participant["token"]), make_envelope(before)).status_code == 202
        assert ingest(client, str(participant["token"]), make_envelope(after)).status_code == 202

        with sqlite3.connect(settings.db_path) as connection:
            connection.execute(
                "UPDATE score_snapshots SET received_at = ? WHERE current_score = 100",
                ("2026-09-19T02:59:00+00:00",),
            )
            connection.execute(
                "UPDATE score_snapshots SET received_at = ? WHERE current_score = 200",
                ("2026-09-19T03:01:00+00:00",),
            )
            connection.execute(
                "UPDATE score_events SET received_at = ? WHERE challenge_code = 'web-1'",
                ("2026-09-19T02:59:00+00:00",),
            )
            connection.execute(
                "UPDATE score_events SET received_at = ? WHERE challenge_code = 'web-2'",
                ("2026-09-19T03:01:00+00:00",),
            )

        board = client.get("/api/v1/public/scoreboard").json()["items"]
        assert board[0]["display_score"] == 100
        assert board[0]["solved"] == 1
        assert board[0]["score_reached_at"] == "2026-09-19T10:58:00+08:00"
        assert board[0]["elapsed_seconds"] == 6480
        series = client.get("/api/v1/public/score-series").json()["series"]
        assert [point["score"] for point in series[0]["points"]] == [100]
        stats = client.get("/api/v1/public/challenge-stats").json()
        assert stats["challenges_solved"] == 1
        assert stats["solve_count"] == 1

        admin_runs = client.get("/api/v1/admin/state", headers=admin_headers()).json()["runs"]
        assert admin_runs[0]["display_score"] == 200


def test_run_started_at_is_immutable_after_first_observation(client: TestClient):
    participant = create_participant(client, "Alice", "ALICE")
    token = str(participant["token"])
    first = make_envelope(
        {
            "run_id": 9001,
            "status": "running",
            "started_at": "2026-09-19T09:02:46+08:00",
            "current_score": 200,
        }
    )
    assert ingest(client, token, first).status_code == 202

    later = make_envelope(
        {
            "run_id": 9001,
            "status": "running",
            "started_at": "2026-09-19T08:56:46+08:00",
            "current_score": 400,
        }
    )
    assert ingest(client, token, later).status_code == 202

    runs = client.get("/api/v1/admin/state", headers=admin_headers()).json()["runs"]
    assert runs[0]["started_at"] == "2026-09-19T09:02:46+08:00"


def test_public_scoring_only_includes_xbow_and_ranks_by_raw_score(tmp_path: Path):
    settings = make_settings(tmp_path, scoring_set_name="XBOW Validation Benchmarks")
    app = create_app(settings=settings, database=Database(settings.db_path))
    with TestClient(app) as client:
        alice = create_participant(client, "Alice", "ALICE")
        bob = create_participant(client, "Bob", "BOB")
        carol = create_participant(client, "Carol", "CAROL")

        for participant, payload in (
            (
                alice,
                {
                    "run_id": 101,
                    "set_id": "1",
                    "set_name": "XBOW Validation Benchmarks",
                    "current_score": 15000,
                    "max_score": 28300,
                },
            ),
            (
                bob,
                {
                    "run_id": 102,
                    "set_id": "1",
                    "set_name": "XBOW Validation Benchmarks",
                    "current_score": 9000,
                    "final_score": 10000,
                    "max_score": 28300,
                },
            ),
            (
                carol,
                {
                    "run_id": 103,
                    "set_id": "9",
                    "set_name": "Other Set",
                    "current_score": 20000,
                    "max_score": 28300,
                },
            ),
        ):
            response = ingest(client, str(participant["token"]), make_envelope(payload))
            assert response.status_code == 202

        board = client.get("/api/v1/public/scoreboard").json()["items"]
        assert [(item["display_name"], item["display_score"]) for item in board] == [
            ("Alice", 15000.0),
            ("Bob", 10000.0),
        ]
        assert (
            client.get("/api/v1/public/score-series").json()["series"][-1]["display_name"] == "Bob"
        )
        stats = client.get("/api/v1/public/challenge-stats").json()
        assert [item["set_name"] for item in stats["sets"]] == ["XBOW Validation Benchmarks"]

        runs = client.get("/api/v1/admin/state", headers=admin_headers()).json()["runs"]
        scoring_by_name = {item["display_name"]: item["scoring"] for item in runs}
        assert scoring_by_name == {"Alice": True, "Bob": True, "Carol": False}


def test_public_challenge_stats(client: TestClient):
    alice = create_participant(client, "Alice", "ALICE")
    bob = create_participant(client, "Bob", "BOB")
    carol = create_participant(client, "Carol", "CAROL")

    alice_run = make_envelope(
        {
            "run_id": 100,
            "status": "running",
            "username": "alice",
            "set_id": "1",
            "set_name": "Primary Set",
            "current_score": 400,
            "max_score": 1000,
            "score_events": [
                {"id": 1, "challenge_code": "web-1", "score_delta": 200},
                {"id": 2, "challenge_code": "web-2", "correct_flag_count": 1},
                {"id": 3, "challenge_code": "web-3", "score_delta": 0, "correct_flag_count": 0},
            ],
        }
    )
    assert ingest(client, str(alice["token"]), alice_run).status_code == 202

    timeline = make_envelope(
        {
            "run_id": 100,
            "items": [
                {
                    "timestamp": "2026-09-18T08:00:00Z",
                    "challenge_code": "web-1",
                    "challenge_score": 200,
                    "cumulative_score": 400,
                }
            ],
        },
        event_type="score_timeline",
    )
    assert ingest(client, str(alice["token"]), timeline).status_code == 202

    bob_run = make_envelope(
        {
            "run_id": 200,
            "status": "running",
            "username": "bob",
            "set_id": "1",
            "set_name": "Primary Set",
            "current_score": 200,
            "max_score": 1000,
            "score_events": [{"id": 11, "challenge_code": "web-1", "score_delta": 200}],
        }
    )
    assert ingest(client, str(bob["token"]), bob_run).status_code == 202

    carol_run = make_envelope(
        {
            "run_id": 300,
            "status": "running",
            "username": "carol",
            "set_id": "9",
            "set_name": "Other Set",
            "current_score": 100,
            "max_score": 1000,
            "score_events": [{"id": 21, "challenge_code": "misc-1", "score_delta": 100}],
        }
    )
    assert ingest(client, str(carol["token"]), carol_run).status_code == 202

    stats = client.get("/api/v1/public/challenge-stats").json()
    assert stats["primary_set_id"] == "1"
    assert stats["primary_set_name"] == "Primary Set"
    assert [item["set_id"] for item in stats["sets"]] == ["1", "9"]
    assert stats["sets"][0]["runners"] == 2
    assert stats["challenges_solved"] == 2
    assert stats["solve_count"] == 3

    board = client.get("/api/v1/public/scoreboard").json()["items"]
    solved_by_name = {item["display_name"]: item["solved"] for item in board}
    assert solved_by_name["Alice"] == 2
    assert solved_by_name["Bob"] == 1
    assert solved_by_name["Carol"] == 1


def test_bulk_participants(client: TestClient):
    unauthorized = client.post(
        "/api/v1/admin/participants/bulk",
        json={"text": "张三 team-01"},
    )
    assert unauthorized.status_code == 401

    text = "张三 team-01\n坏行\n李四 team-02\n王五 bad!\n"
    response = client.post(
        "/api/v1/admin/participants/bulk",
        headers=admin_headers(),
        json={"text": text},
    )
    assert response.status_code == 200
    result = response.json()
    assert len(result["created"]) == 2
    by_name = {item["display_name"]: item for item in result["created"]}
    assert by_name["张三"]["code"] == "team-01"
    assert by_name["张三"]["token"].startswith("tsp_")
    assert by_name["李四"]["code"] == "team-02"
    assert by_name["李四"]["token"].startswith("tsp_")
    assert {error["line"] for error in result["errors"]} == {2, 4}

    session = client.get(
        "/api/v1/participant/session",
        headers={"Authorization": "Bearer " + by_name["张三"]["token"]},
    )
    assert session.status_code == 200
    assert session.json()["display_name"] == "张三"

    repeated = client.post(
        "/api/v1/admin/participants/bulk",
        headers=admin_headers(),
        json={"text": "张三 team-01"},
    ).json()
    assert repeated["created"] == []
    assert len(repeated["skipped"]) == 1
    assert repeated["skipped"][0]["reason"] == "编号已存在"

    conflict = client.post(
        "/api/v1/admin/participants/bulk",
        headers=admin_headers(),
        json={"text": "张三 team-03"},
    ).json()
    assert conflict["created"][0]["code"] == "team-03"
    assert conflict["created"][0]["token"] != by_name["张三"]["token"]

    state = client.get("/api/v1/admin/state", headers=admin_headers()).json()
    assert state["counts"]["participants"] == 3
    assert all("token" not in participant for participant in state["participants"])


def test_create_participant_with_custom_token(client: TestClient):
    response = client.post(
        "/api/v1/admin/participants",
        headers=admin_headers(),
        json={"display_name": "测试选手", "code": "test", "token": "test123456"},
    )
    assert response.status_code == 201
    assert response.json()["token"] == "test123456"

    session = client.get(
        "/api/v1/participant/session",
        headers={"Authorization": "Bearer test123456"},
    )
    assert session.status_code == 200
    assert session.json()["display_name"] == "测试选手"

    duplicate = client.post(
        "/api/v1/admin/participants",
        headers=admin_headers(),
        json={"display_name": "另一名", "code": "other", "token": "test123456"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "participant token already exists"

    invalid = client.post(
        "/api/v1/admin/participants",
        headers=admin_headers(),
        json={"display_name": "无效", "code": "invalid", "token": "a b"},
    )
    assert invalid.status_code == 422

    state = client.get("/api/v1/admin/state", headers=admin_headers()).json()
    assert all("token" not in participant for participant in state["participants"])


def test_scoring_window_filters_runs_started_outside_window(windowed_client: TestClient):
    client = windowed_client
    config = client.get("/api/v1/public/config").json()
    assert config["scoring_start"] == "2030-01-01T09:00:00+08:00"
    assert config["scoring_end"] == "2030-01-01T11:00:00+08:00"

    participant = create_participant(client, "Alice", "ALICE")
    token = str(participant["token"])
    envelope = make_envelope(
        {
            "active_run": {
                "run_id": 700,
                "status": "running",
                "current_score": 800,
                "max_score": 28300,
            }
        },
        event_type="active_run",
    )
    assert ingest(client, token, envelope).status_code == 202

    board = client.get("/api/v1/public/scoreboard").json()
    assert board["items"] == []
    assert client.get("/api/v1/public/score-series").json()["series"] == []
    assert client.get("/api/v1/public/challenge-stats").json()["sets"] == []

    state = client.get("/api/v1/admin/state", headers=admin_headers()).json()
    assert state["counts"]["runs"] == 1
    assert state["runs"][0]["scoring"] is False

    database = client.app.state.database
    with database.lock, database.connection:
        database.connection.execute(
            "UPDATE runs SET started_at = ?", ("2030-01-01T10:00:00+08:00",)
        )

    board = client.get("/api/v1/public/scoreboard").json()
    assert len(board["items"]) == 1
    assert board["items"][0]["display_name"] == "Alice"
    series = client.get("/api/v1/public/score-series").json()["series"]
    assert len(series) == 1
    assert [point["score"] for point in series[0]["points"]] == [800.0]

    state = client.get("/api/v1/admin/state", headers=admin_headers()).json()
    assert state["runs"][0]["scoring"] is True

    with database.lock, database.connection:
        database.connection.execute(
            "UPDATE runs SET started_at = ?", ("2030-01-01T12:00:00+08:00",)
        )
    board = client.get("/api/v1/public/scoreboard").json()
    assert board["items"] == []
    state = client.get("/api/v1/admin/state", headers=admin_headers()).json()
    assert state["runs"][0]["scoring"] is False


def test_active_run_timeline_and_manual_verification(client: TestClient):
    participant = create_participant(client, "Alice", "ALICE")
    token = str(participant["token"])
    active = make_envelope(
        {
            "active_run": {
                "run_id": 500,
                "status": "running",
                "current_score": 0,
                "max_score": 100,
            }
        },
        event_type="active_run",
    )
    assert ingest(client, token, active).status_code == 202

    timeline = make_envelope(
        {
            "run_id": 500,
            "items": [
                {
                    "timestamp": "2026-09-18T08:00:00Z",
                    "challenge_code": "misc-1",
                    "challenge_score": 15,
                    "cumulative_score": 15,
                }
            ],
        },
        event_type="score_timeline",
    )
    assert ingest(client, token, timeline).status_code == 202

    heartbeat = make_envelope(
        {"run_id": 500, "route": "/benchmark/running/500", "status": "page_visible"},
        event_type="heartbeat",
    )
    assert ingest(client, token, heartbeat).status_code == 202

    state = client.get("/api/v1/admin/state", headers=admin_headers()).json()
    assert state["runs"][0]["status"] == "running"
    database_run_id = state["runs"][0]["id"]
    updated = client.patch(
        f"/api/v1/admin/runs/{database_run_id}/verification",
        headers=admin_headers(),
        json={"verification_status": "verified"},
    )
    assert updated.status_code == 200
    board = client.get("/api/v1/public/scoreboard").json()["items"]
    assert board[0]["verification_status"] == "verified"

    changed_ip = make_envelope(
        {
            "active_run": {
                "run_id": 500,
                "status": "running",
                "current_score": 15,
                "max_score": 100,
            }
        },
        event_type="active_run",
    )
    changed = ingest(client, token, changed_ip, ip="198.51.100.20")
    assert changed.status_code == 202
    assert "ip_changed" in changed.json()["risk_flags"]
    board = client.get("/api/v1/public/scoreboard").json()["items"]
    assert board[0]["verification_status"] == "verified"
    assert board[0]["risk_count"] == 0
    state = client.get("/api/v1/admin/state", headers=admin_headers()).json()
    assert state["runs"][0]["risk_flags"] == []


class FakeIpRegion:
    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping

    def location_text(self, ip):
        return self.mapping.get(ip)

    def is_outside_province(self, ip, province):
        location = self.mapping.get(ip)
        return location is not None and province not in location


def test_ip_region_marks_outside_province_and_reports_location(tmp_path: Path):
    settings = make_settings(tmp_path)
    resolver = FakeIpRegion(
        {
            "203.0.113.77": "中国 甲省 甲市",
            "198.51.100.77": "中国 乙省 乙市",
        }
    )
    app = create_app(
        settings=settings,
        database=Database(settings.db_path, ip_region=resolver, expected_province="甲省"),
    )
    with TestClient(app) as client:
        token = str(create_participant(client, "张三", "zhangsan")["token"])

        outside = make_envelope(
            {"active_run": {"run_id": 900, "status": "running", "current_score": 10}},
            event_type="active_run",
        )
        response = ingest(client, token, outside, ip="198.51.100.77")
        assert response.status_code == 202
        assert "ip_region_outside" in response.json()["risk_flags"]

        local = make_envelope(
            {"active_run": {"run_id": 901, "status": "running", "current_score": 10}},
            event_type="active_run",
            installation_id="install-test-0002",
        )
        response = ingest(client, token, local, ip="203.0.113.77")
        assert response.status_code == 202
        assert "ip_region_outside" not in response.json()["risk_flags"]

        runs = client.get("/api/v1/admin/state", headers=admin_headers()).json()["runs"]
        by_ip = {run["last_ip"]: run for run in runs}
        assert by_ip["198.51.100.77"]["ip_location"] == "中国 乙省 乙市"
        assert "ip_region_outside" in by_ip["198.51.100.77"]["risk_flags"]
        assert by_ip["198.51.100.77"]["verification_status"] == "risk"
        assert by_ip["203.0.113.77"]["ip_location"] == "中国 甲省 甲市"
        assert by_ip["203.0.113.77"]["verification_status"] != "risk"


def test_ip_region_disabled_keeps_runs_clean(client: TestClient):
    token = str(create_participant(client, "李四", "lisi")["token"])
    envelope = make_envelope(
        {"active_run": {"run_id": 902, "status": "running", "current_score": 5}},
        event_type="active_run",
    )
    response = ingest(client, token, envelope, ip="198.51.100.77")
    assert response.status_code == 202
    assert "ip_region_outside" not in response.json()["risk_flags"]
    runs = client.get("/api/v1/admin/state", headers=admin_headers()).json()["runs"]
    assert runs[0]["ip_location"] is None


def test_config_for_new_event(monkeypatch):
    for key in (
        "TSEC_SCORING_START",
        "TSEC_SCORING_END",
        "TSEC_SCORING_SET_NAME",
        "TSEC_EXPECTED_PROVINCE",
        "TSEC_STORE_RAW_IP",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings.from_env()
    assert settings.scoring_start is settings.scoring_end is settings.scoring_set_name is None
    assert settings.expected_province is None
    assert not settings.store_raw_ip
    monkeypatch.setenv("TSEC_SCORING_START", "2030-02-03T09:00:00")
    with pytest.raises(ValueError, match="timezone"):
        Settings.from_env()
    monkeypatch.setenv("TSEC_SCORING_START", "2030-02-03T09:00:00+08:00")
    monkeypatch.setenv("TSEC_SCORING_END", "2030-02-03T00:30:00Z")
    with pytest.raises(ValueError, match="after"):
        Settings.from_env()
