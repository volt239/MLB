from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


class Team(BaseModel):
    name: str
    score: int
    hits: int
    errors: int


class GameState(BaseModel):
    game_id: str
    status: str
    inning: int
    half: str
    balls: int
    strikes: int
    outs: int
    home: Team
    away: Team
    last_update: str
    probable_pitcher_home: str
    probable_pitcher_away: str
    probable_pitcher_home_era: str
    probable_pitcher_away_era: str
    current_batter: str
    current_pitcher: str
    current_batter_position: str
    current_pitcher_position: str
    current_batter_avg: str
    current_pitcher_era: str
    home_lineup_preview: list[str]
    away_lineup_preview: list[str]


class MlbLiveProvider:
    BASE_URL = "https://statsapi.mlb.com"
    TRACKED_TEAMS = ("Los Angeles Dodgers", "San Diego Padres")
    TRACKED_KEYWORDS = ("dodgers", "padres")

    def __init__(self) -> None:
        self.states = [self._build_no_game_state(name) for name in self.TRACKED_TEAMS]
        self._client: httpx.AsyncClient | None = None

    @property
    def state(self) -> GameState:
        return self.states[0]

    async def start(self) -> None:
        self._client = httpx.AsyncClient(base_url=self.BASE_URL, timeout=8.0)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def refresh(self) -> list[GameState]:
        if self._client is None:
            return self.states

        try:
            games = await self._get_candidate_games()
            target_games = self._pick_target_games(games)

            if not target_games:
                self.states = [self._build_no_game_state(name) for name in self.TRACKED_TEAMS]
                return self.states

            feed_cache: dict[int, GameState] = {}
            parsed_states: list[GameState] = []
            for game in target_games:
                game_pk = game.get("gamePk")
                if not isinstance(game_pk, int):
                    continue

                if game_pk not in feed_cache:
                    feed = await self._fetch_json(f"/api/v1.1/game/{game_pk}/feed/live")
                    feed_cache[game_pk] = self._parse_feed(feed)

                parsed_states.append(feed_cache[game_pk])

            if not parsed_states:
                self.states = [self._build_no_game_state(name) for name in self.TRACKED_TEAMS]
                return self.states

            self.states = parsed_states[:2]
            return self.states

        except Exception:
            now = datetime.now(timezone.utc).isoformat()
            for state in self.states:
                state.last_update = now
            return self.states

    async def _get_candidate_games(self) -> list[dict[str, Any]]:
        today_et = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
        all_games: list[dict[str, Any]] = []

        for day_offset in (0, -1, 1):
            target_date = today_et + timedelta(days=day_offset)
            data = await self._fetch_json(
                "/api/v1/schedule",
                params={"sportId": 1, "date": str(target_date)},
            )
            for date_blob in data.get("dates", []):
                all_games.extend(date_blob.get("games", []))

        return all_games

    @staticmethod
    def _status_priority(abstract_state: str) -> int:
        priorities = {
            "Live": 0,
            "Manager Challenge": 1,
            "Delayed": 2,
            "Suspended": 3,
            "Preview": 4,
            "Final": 5,
        }
        return priorities.get(abstract_state, 9)

    def _pick_target_games(self, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tracked = [game for game in games if self._is_tracked_game(game)]
        if not tracked:
            return []

        tracked_sorted = sorted(
            tracked,
            key=lambda g: (
                self._status_priority(g.get("status", {}).get("abstractGameState", "")),
                g.get("gameDate", "9999-12-31T23:59:59Z"),
            ),
        )

        unique: list[dict[str, Any]] = []
        seen: set[int] = set()
        for game in tracked_sorted:
            game_pk = game.get("gamePk")
            if not isinstance(game_pk, int) or game_pk in seen:
                continue
            seen.add(game_pk)
            unique.append(game)
            if len(unique) >= 2:
                break

        return unique

    def _is_tracked_game(self, game: dict[str, Any]) -> bool:
        teams = game.get("teams", {})
        home_name = (
            teams.get("home", {}).get("team", {}).get("name", "").strip().lower()
        )
        away_name = (
            teams.get("away", {}).get("team", {}).get("name", "").strip().lower()
        )

        for keyword in self.TRACKED_KEYWORDS:
            if keyword in home_name or keyword in away_name:
                return True
        return False

    async def _fetch_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._client is None:
            return {}
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def _parse_feed(self, feed: dict[str, Any]) -> GameState:
        game_data = feed.get("gameData", {})
        live_data = feed.get("liveData", {})
        boxscore = live_data.get("boxscore", {})
        linescore = live_data.get("linescore", {})

        home_team_data = game_data.get("teams", {}).get("home", {})
        away_team_data = game_data.get("teams", {}).get("away", {})

        home_line = linescore.get("teams", {}).get("home", {})
        away_line = linescore.get("teams", {}).get("away", {})

        status_detail = game_data.get("status", {}).get("detailedState", "Unknown")

        inning_half = linescore.get("inningHalf") or linescore.get("inningState") or "-"
        if isinstance(inning_half, str):
            inning_half = inning_half.upper()

        probable_home = self._extract_probable_pitcher(game_data, "home")
        probable_away = self._extract_probable_pitcher(game_data, "away")
        current_batter = self._extract_current_player(live_data, "batter")
        current_pitcher = self._extract_current_player(live_data, "pitcher")

        return GameState(
            game_id=str(game_data.get("game", {}).get("pk", "MLB-UNKNOWN")),
            status=status_detail,
            inning=linescore.get("currentInning", 0) or 0,
            half=inning_half,
            balls=linescore.get("balls", 0) or 0,
            strikes=linescore.get("strikes", 0) or 0,
            outs=linescore.get("outs", 0) or 0,
            home=Team(
                name=home_team_data.get("name", "Home"),
                score=home_line.get("runs", 0) or 0,
                hits=home_line.get("hits", 0) or 0,
                errors=home_line.get("errors", 0) or 0,
            ),
            away=Team(
                name=away_team_data.get("name", "Away"),
                score=away_line.get("runs", 0) or 0,
                hits=away_line.get("hits", 0) or 0,
                errors=away_line.get("errors", 0) or 0,
            ),
            last_update=datetime.now(timezone.utc).isoformat(),
            probable_pitcher_home=probable_home["name"],
            probable_pitcher_away=probable_away["name"],
            probable_pitcher_home_era=probable_home["era"],
            probable_pitcher_away_era=probable_away["era"],
            current_batter=current_batter["name"],
            current_pitcher=current_pitcher["name"],
            current_batter_position=current_batter["position"],
            current_pitcher_position=current_pitcher["position"],
            current_batter_avg=current_batter["avg"],
            current_pitcher_era=current_pitcher["era"],
            home_lineup_preview=self._extract_lineup_preview(
                boxscore.get("teams", {}).get("home", {})
            ),
            away_lineup_preview=self._extract_lineup_preview(
                boxscore.get("teams", {}).get("away", {})
            ),
        )

    def _build_no_game_state(self, tracked_team_name: str) -> GameState:
        return GameState(
            game_id="MLB-NO-GAME",
            status="NO GAME",
            inning=0,
            half="-",
            balls=0,
            strikes=0,
            outs=0,
            home=Team(name=tracked_team_name, score=0, hits=0, errors=0),
            away=Team(name="No Opponent", score=0, hits=0, errors=0),
            last_update=datetime.now(timezone.utc).isoformat(),
            probable_pitcher_home="-",
            probable_pitcher_away="-",
            probable_pitcher_home_era="-",
            probable_pitcher_away_era="-",
            current_batter="-",
            current_pitcher="-",
            current_batter_position="-",
            current_pitcher_position="-",
            current_batter_avg="-",
            current_pitcher_era="-",
            home_lineup_preview=[],
            away_lineup_preview=[],
        )

    def _extract_probable_pitcher(self, game_data: dict[str, Any], side: str) -> dict[str, str]:
        pitcher = game_data.get("probablePitchers", {}).get(side, {})
        name = pitcher.get("fullName", "-")
        era = "-"
        pitcher_id = pitcher.get("id")
        if isinstance(pitcher_id, int):
            era = self._extract_player_pitching_era_from_boxscore_by_id(
                game_data, side, pitcher_id
            )
        return {"name": name, "era": era}

    def _extract_current_player(self, live_data: dict[str, Any], role: str) -> dict[str, str]:
        matchup = live_data.get("plays", {}).get("currentPlay", {}).get("matchup", {})
        player = matchup.get(role, {})
        player_name = player.get("fullName", "-")
        player_id = player.get("id")

        if role == "batter":
            position = self._extract_player_position(live_data, player_id)
            avg = self._extract_player_batting_avg(live_data, player_id)
            return {"name": player_name, "position": position, "avg": avg, "era": "-"}

        position = self._extract_player_position(live_data, player_id)
        era = self._extract_player_pitching_era(live_data, player_id)
        return {"name": player_name, "position": position, "avg": "-", "era": era}

    @staticmethod
    def _extract_lineup_preview(team_box: dict[str, Any], limit: int = 3) -> list[str]:
        players = team_box.get("players", {})
        collected: list[tuple[int, str, str, str]] = []
        for player in players.values():
            order_raw = player.get("battingOrder")
            name = player.get("person", {}).get("fullName")
            if not order_raw or not name:
                continue
            try:
                order = int(order_raw)
            except (TypeError, ValueError):
                continue
            position = player.get("position", {}).get("abbreviation", "-")
            avg = player.get("stats", {}).get("batting", {}).get("avg", "-")
            collected.append((order, name, position, avg))

        collected.sort(key=lambda item: item[0])
        return [f"{name} ({position}, AVG {avg})" for _, name, position, avg in collected[:limit]]

    @staticmethod
    def _extract_player_position(live_data: dict[str, Any], player_id: Any) -> str:
        if not isinstance(player_id, int):
            return "-"
        players = live_data.get("boxscore", {}).get("teams", {})
        for side in ("home", "away"):
            team_players = players.get(side, {}).get("players", {})
            player_blob = team_players.get(f"ID{player_id}", {})
            if player_blob:
                return player_blob.get("position", {}).get("abbreviation", "-")
        return "-"

    @staticmethod
    def _extract_player_batting_avg(live_data: dict[str, Any], player_id: Any) -> str:
        if not isinstance(player_id, int):
            return "-"
        players = live_data.get("boxscore", {}).get("teams", {})
        for side in ("home", "away"):
            team_players = players.get(side, {}).get("players", {})
            player_blob = team_players.get(f"ID{player_id}", {})
            if player_blob:
                return player_blob.get("stats", {}).get("batting", {}).get("avg", "-")
        return "-"

    @staticmethod
    def _extract_player_pitching_era(live_data: dict[str, Any], player_id: Any) -> str:
        if not isinstance(player_id, int):
            return "-"
        players = live_data.get("boxscore", {}).get("teams", {})
        for side in ("home", "away"):
            team_players = players.get(side, {}).get("players", {})
            player_blob = team_players.get(f"ID{player_id}", {})
            if player_blob:
                return player_blob.get("stats", {}).get("pitching", {}).get("era", "-")
        return "-"

    def _extract_player_pitching_era_from_boxscore_by_id(
        self, game_data: dict[str, Any], side: str, player_id: int
    ) -> str:
        # Fallback: probable pitcher ERA is often unavailable pre-game in feed/gameData.
        # Keep '-' unless we can locate it from current live boxscore path in match context.
        _ = game_data
        _ = side
        _ = player_id
        return "-"


class ConnectionHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.clients.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.clients.discard(websocket)

    async def broadcast(self, payload: dict) -> None:
        stale: list[WebSocket] = []
        for client in self.clients:
            try:
                await client.send_json(payload)
            except Exception:
                stale.append(client)

        for dead in stale:
            self.disconnect(dead)


provider = MlbLiveProvider()
hub = ConnectionHub()

app = FastAPI(title="Realtime Baseball Dashboard API", version="0.3.0")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/game")
async def game_snapshot() -> dict:
    return provider.state.model_dump()


@app.get("/api/games")
async def games_snapshot() -> dict:
    return {"games": [state.model_dump() for state in provider.states]}


@app.websocket("/ws")
async def ws_live_game(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    try:
        await websocket.send_json({"games": [state.model_dump() for state in provider.states]})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)


async def ticker() -> None:
    poll_seconds = max(3, int(os.getenv("MLB_POLL_SECONDS", "8")))
    while True:
        await asyncio.sleep(poll_seconds)
        states = await provider.refresh()
        await hub.broadcast({"games": [state.model_dump() for state in states]})


@app.on_event("startup")
async def on_startup() -> None:
    await provider.start()
    await provider.refresh()
    app.state.ticker_task = asyncio.create_task(ticker())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    task = getattr(app.state, "ticker_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await provider.stop()
