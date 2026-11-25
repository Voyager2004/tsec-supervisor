import asyncio
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from time import monotonic
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .database import Database
from .ip_region import make_resolver
from .normalizer import normalize_payload
from .participants import parse_bulk_text
from .schemas import (
    IngestEnvelope,
    ParticipantBulkCreate,
    ParticipantCreate,
    ParticipantUpdate,
    VerificationUpdate,
)
from .security import (
    SlidingWindowLimiter,
    bearer_token,
    extract_client_ip,
    ip_digest,
    require_admin,
    token_hash,
)

MAX_BODY_BYTES = 256_000


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = database or Database(
        settings.db_path, ip_region=make_resolver(settings.ip2region_path),
        expected_province=settings.expected_province,
    )
    limiter = SlidingWindowLimiter(limit=240, window_seconds=60)
    public_cache: dict[str, tuple[float, Any]] = {}

    def cached_public(key: str, ttl_seconds: float, loader: Callable[[], Any]) -> Any:
        if settings.environment != "production":
            return loader()
        now = monotonic()
        cached = public_cache.get(key)
        if cached is not None and now - cached[0] < ttl_seconds:
            return cached[1]
        value = loader()
        public_cache[key] = (now, value)
        return value

    async def retention_loop() -> None:
        while True:
            await asyncio.sleep(6 * 60 * 60)
            database.prune(settings.retention_days)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.prune(settings.retention_days)
        retention_task = asyncio.create_task(retention_loop())
        try:
            yield
        finally:
            retention_task.cancel()
            with suppress(asyncio.CancelledError):
                await retention_task
            database.close()

    app = FastAPI(
        title="Tsec Supervisor",
        version=__version__,
        docs_url="/api/docs" if settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.limiter = limiter

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        max_age=600,
    )

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_BODY_BYTES:
                    return Response(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
            except ValueError:
                return Response(status_code=status.HTTP_400_BAD_REQUEST)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api/v1/admin") else "no-cache"
        )
        response.headers["X-Request-ID"] = request.headers.get(
            "x-request-id", secrets.token_hex(8)
        )[:80]
        return response

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def get_settings() -> Settings:
        return settings

    def get_database() -> Database:
        return database

    def admin_guard(request: Request) -> None:
        require_admin(request, settings)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/public/config")
    async def public_config(
        current: Annotated[Settings, Depends(get_settings)],
    ) -> dict[str, str | int | None]:
        return {
            "event_name": current.event_name,
            "refresh_seconds": 3,
            "scoring_start": current.scoring_start,
            "scoring_end": current.scoring_end,
            "scoring_set_name": current.scoring_set_name,
        }

    @app.get("/api/v1/public/scoreboard")
    async def scoreboard(
        db: Annotated[Database, Depends(get_database)],
        current: Annotated[Settings, Depends(get_settings)],
    ) -> dict[str, object]:
        return cached_public(
            "scoreboard",
            1.5,
            lambda: {
                "items": db.scoreboard(
                    current.scoring_start, current.scoring_end, current.scoring_set_name
                )
            },
        )

    @app.get("/api/v1/public/score-series")
    async def score_series(
        db: Annotated[Database, Depends(get_database)],
        current: Annotated[Settings, Depends(get_settings)],
    ) -> dict[str, object]:
        return cached_public(
            "score-series",
            10.0,
            lambda: {
                "series": db.score_series(
                    current.scoring_start, current.scoring_end, current.scoring_set_name
                )
            },
        )

    @app.get("/api/v1/public/challenge-stats")
    async def challenge_stats(
        db: Annotated[Database, Depends(get_database)],
        current: Annotated[Settings, Depends(get_settings)],
    ) -> dict[str, object]:
        return cached_public(
            "challenge-stats",
            3.0,
            lambda: db.challenge_stats(
                current.scoring_start, current.scoring_end, current.scoring_set_name
            ),
        )

    @app.get("/api/v1/participant/session")
    async def participant_session(
        request: Request,
        db: Annotated[Database, Depends(get_database)],
    ) -> dict[str, object]:
        participant = db.authenticate_participant(bearer_token(request))
        if participant is None:
            raise HTTPException(status_code=401, detail="invalid participant token")
        return {
            "id": participant["id"],
            "code": participant["code"],
            "display_name": participant["display_name"],
            "active": bool(participant["active"]),
        }

    @app.post("/api/v1/ingest", status_code=status.HTTP_202_ACCEPTED)
    async def ingest(
        request: Request,
        envelope: IngestEnvelope,
        db: Annotated[Database, Depends(get_database)],
        current: Annotated[Settings, Depends(get_settings)],
    ) -> dict[str, object]:
        token = bearer_token(request)
        participant = db.authenticate_participant(token)
        if participant is None:
            raise HTTPException(status_code=401, detail="invalid participant token")
        client_ip = extract_client_ip(request, current)
        limiter.check(f"{token_hash(token)}:{client_ip}")
        try:
            payload = normalize_payload(envelope.event_type, envelope.payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        digest = ip_digest(client_ip, current.ip_hash_salt)
        return db.ingest(
            participant,
            envelope,
            payload,
            raw_ip=client_ip if current.store_raw_ip else None,
            ip_hash=digest,
        )

    @app.get("/api/v1/admin/state", dependencies=[Depends(admin_guard)])
    async def admin_state(
        db: Annotated[Database, Depends(get_database)],
        current: Annotated[Settings, Depends(get_settings)],
    ) -> dict[str, object]:
        return db.admin_state(
            current.scoring_start, current.scoring_end, current.scoring_set_name
        )

    @app.post(
        "/api/v1/admin/participants",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(admin_guard)],
    )
    async def create_participant(
        body: ParticipantCreate,
        db: Annotated[Database, Depends(get_database)],
    ) -> dict[str, object]:
        try:
            return db.create_participant(body.display_name, body.code, token=body.token)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/admin/participants/bulk",
        dependencies=[Depends(admin_guard)],
    )
    async def create_participants_bulk(
        body: ParticipantBulkCreate,
        db: Annotated[Database, Depends(get_database)],
    ) -> dict[str, object]:
        entries, errors = parse_bulk_text(body.text)
        result = db.create_participants_bulk(entries)
        return {
            "created": result["created"],
            "skipped": result["skipped"],
            "errors": errors,
        }

    @app.patch(
        "/api/v1/admin/participants/{participant_id}",
        dependencies=[Depends(admin_guard)],
    )
    async def update_participant(
        participant_id: int,
        body: ParticipantUpdate,
        db: Annotated[Database, Depends(get_database)],
    ) -> dict[str, bool]:
        try:
            db.update_participant(
                participant_id,
                display_name=body.display_name,
                active=body.active,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"updated": True}

    @app.post(
        "/api/v1/admin/participants/{participant_id}/rotate-token",
        dependencies=[Depends(admin_guard)],
    )
    async def rotate_token(
        participant_id: int,
        db: Annotated[Database, Depends(get_database)],
    ) -> dict[str, str]:
        try:
            token = db.rotate_participant_token(participant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"token": token}

    @app.delete(
        "/api/v1/admin/participants/{participant_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(admin_guard)],
    )
    async def delete_participant(
        participant_id: int,
        db: Annotated[Database, Depends(get_database)],
    ) -> Response:
        try:
            db.delete_participant(participant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.patch(
        "/api/v1/admin/runs/{database_run_id}/verification",
        dependencies=[Depends(admin_guard)],
    )
    async def update_verification(
        database_run_id: int,
        body: VerificationUpdate,
        db: Annotated[Database, Depends(get_database)],
    ) -> dict[str, bool]:
        try:
            db.update_verification(database_run_id, body.verification_status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"updated": True}

    return app


app = create_app()
