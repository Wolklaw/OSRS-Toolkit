from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from toolkit_web.deps import Deps
from toolkit_web.upscape_scores_db import MODES, UpscapeScoresDB

# Upscape's own player ID: 40 random bits, Crockford base32, no personal information (see Upscape's js/shop.js).
PLAYER_ID_RE = r"^[0-9A-Z]{1,16}$"
SUBMIT_WINDOW_SECONDS = 600
SUBMIT_MAX_PER_WINDOW = 30


class ScoreSubmission(BaseModel):
    player_id: str = Field(pattern=PLAYER_ID_RE)
    name: str = Field(min_length=1, max_length=20)
    mode: str
    score: int = Field(ge=0, le=100_000_000)
    floor: int = Field(ge=0, le=20_000)
    combo: int = Field(ge=0, le=20_000)
    climber: str = Field(min_length=1, max_length=20)
    version: str | None = Field(default=None, max_length=20)


def build(deps: Deps) -> FastAPI:
    # Its own FastAPI app, mounted at /api/upscape in app.py rather than included as a router, so CORS can be opened up
    # for just this one game's two endpoints -- the rest of runescope.app (everyone's trading journal) stays same-origin
    # only. See store/CODES.md and docs/ROADMAP.md in the Upscape repo for why this exists and what it does and doesn't
    # trust from the client: scores are accepted as reported, the same trust level Play Games leaderboards already have.
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    db = UpscapeScoresDB(deps.settings.upscape_scores_db_path)
    submit_times: dict[str, deque[float]] = defaultdict(deque)
    lock = threading.Lock()

    def rate_limited(client_ip: str) -> bool:
        now = time.monotonic()
        with lock:
            times = submit_times[client_ip]
            while times and now - times[0] > SUBMIT_WINDOW_SECONDS:
                times.popleft()
            if len(times) >= SUBMIT_MAX_PER_WINDOW:
                return True
            times.append(now)
            return False

    @app.post("/scores")
    def submit_score(submission: ScoreSubmission, request: Request) -> dict:
        if submission.mode not in MODES:
            raise HTTPException(status_code=400, detail="Unknown mode")
        if rate_limited(request.client.host if request.client else "unknown"):
            raise HTTPException(status_code=429, detail="Too many submissions")
        name = submission.name.strip()[:20] or "PLAYER"
        saved = db.submit(
            submission.player_id,
            submission.mode,
            name,
            submission.score,
            submission.floor,
            submission.combo,
            submission.climber,
            submission.version,
        )
        return {"ok": True, "saved": saved}

    @app.get("/leaderboard")
    def leaderboard(mode: str, limit: int = 10) -> dict:
        if mode not in MODES:
            raise HTTPException(status_code=400, detail="Unknown mode")
        limit = max(1, min(limit, 50))
        rows = db.top(mode, limit)
        return {
            "scores": [
                {
                    "name": row.name,
                    "score": row.score,
                    "floor": row.floor,
                    "combo": row.combo,
                    "climber": row.climber,
                }
                for row in rows
            ]
        }

    return app
