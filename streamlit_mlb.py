from __future__ import annotations

import asyncio
from html import escape

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from app.main import MlbLiveProvider


TEAM_COLORS = {
    "LOSANGELESDODGERS": "#005A9C",
    "SANDIEGOPADRES": "#2F241D",
    "WASHINGTONNATIONALS": "#AB0003",
    "BOSTONREDSOX": "#BD3039",
    "SANFRANCISCOGIANTS": "#FD5A1E",
    "NEWYORKYANKEES": "#132448",
    "CHICAGOCUBS": "#0E3386",
    "STLOUISCARDINALS": "#C41E3A",
}


def run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def normalize_team_name(name: str) -> str:
    return (name or "").upper().replace(" ", "")


def team_color(name: str) -> str:
    return TEAM_COLORS.get(normalize_team_name(name), "#334155")


def mix_hex(a: str, b: str, ratio: float = 0.5) -> str:
    ah = a.replace("#", "")
    bh = b.replace("#", "")
    ar, ag, ab = int(ah[0:2], 16), int(ah[2:4], 16), int(ah[4:6], 16)
    br, bg, bb = int(bh[0:2], 16), int(bh[2:4], 16), int(bh[4:6], 16)
    rr = round(ar + (br - ar) * ratio)
    rg = round(ag + (bg - ag) * ratio)
    rb = round(ab + (bb - ab) * ratio)
    return f"#{rr:02x}{rg:02x}{rb:02x}"


def matchup_gradient(away_name: str, home_name: str) -> str:
    away = team_color(away_name)
    home = team_color(home_name)
    mid = mix_hex(away, home, 0.5)
    return (
        f"linear-gradient(90deg, {away} 0%, {away} 38%, "
        f"{mid} 50%, {home} 62%, {home} 100%)"
    )


def base_mask(game: dict) -> int:
    return (
        (1 if int(game.get("base1", 0) or 0) > 0 else 0)
        | (2 if int(game.get("base2", 0) or 0) > 0 else 0)
        | (4 if int(game.get("base3", 0) or 0) > 0 else 0)
    )


def style() -> None:
    st.markdown(
        """
<style>
:root {
  --bg:#0f172a; --line:#334155; --text:#e2e8f0; --muted:#94a3b8;
}
html, body, [class*="css"] { background: radial-gradient(circle at top, #1e293b 0%, var(--bg) 60%); color: var(--text); }
.block-container { max-width: 1080px; padding-top: 1.4rem; }
.cards { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }
.card { border:1px solid var(--line); border-radius:14px; padding:16px; box-shadow:0 10px 30px rgba(0,0,0,.22); }
.scoreboard { display:grid; grid-template-columns:1fr auto 1fr; gap:8px; align-items:center; }
.team { text-align:center; }
.team h3 { margin:0 0 4px; font-size:17px; }
.score { font-size:44px; font-weight:800; line-height:1; }
.mid { text-align:center; color:var(--muted); font-size:13px; }
.meta { margin-top:12px; display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }
.meta-box { background:rgba(2,6,23,.5); border:1px solid var(--line); border-radius:10px; padding:8px; text-align:center; }
.label { font-size:10px; color:var(--muted); margin-bottom:2px; }
.val { font-size:16px; font-weight:700; }
.players { margin-top:10px; border-top:1px dashed var(--line); padding-top:10px; display:grid; gap:6px; }
.row { font-size:12px; color:var(--text); }
.k { color:var(--muted); }
.sub { margin-top:10px; color:var(--muted); font-size:11px; }
.diamond-wrap { display:flex; align-items:center; justify-content:center; }
.diamond { position:relative; width:56px; height:56px; }
.base { position:absolute; width:11px; height:11px; border:1px solid rgba(255,255,255,.75); background:rgba(15,23,42,.45); border-radius:2px; transform:translate(-50%,-50%) rotate(45deg); }
.base.on { background:#fde047; border-color:#facc15; box-shadow:0 0 8px rgba(250,204,21,.8); animation:basePulse 1.2s ease-in-out infinite; }
.base.flash { animation:baseFlash .45s ease-out 1, basePulse 1.2s ease-in-out infinite; }
.base.second { top:12px; left:50%; }
.base.third { top:50%; left:12px; }
.base.first { top:50%; left:calc(100% - 12px); }
.base.home { top:calc(100% - 12px); left:50%; background:rgba(148,163,184,.5); }
@keyframes basePulse { 0%,100%{ box-shadow:0 0 6px rgba(250,204,21,.65);} 50%{ box-shadow:0 0 14px rgba(250,204,21,.95);} }
@keyframes baseFlash { 0%{ transform:translate(-50%,-50%) rotate(45deg) scale(1);} 35%{ transform:translate(-50%,-50%) rotate(45deg) scale(1.25); box-shadow:0 0 18px rgba(255,255,255,.95);} 100%{ transform:translate(-50%,-50%) rotate(45deg) scale(1);} }
@media (max-width:900px){ .cards{ grid-template-columns:1fr; } .score{ font-size:36px; } }
</style>
        """,
        unsafe_allow_html=True,
    )


def render_game(game: dict, changed_mask: int) -> str:
    away = escape(str(game.get("away", {}).get("name", "Away")))
    home = escape(str(game.get("home", {}).get("name", "Home")))
    status = escape(str(game.get("status", "-")))
    half = escape(str(game.get("half", "-")))
    inning = game.get("inning", 0)

    away_score = game.get("away", {}).get("score", 0)
    away_hits = game.get("away", {}).get("hits", 0)
    away_err = game.get("away", {}).get("errors", 0)
    home_score = game.get("home", {}).get("score", 0)
    home_hits = game.get("home", {}).get("hits", 0)
    home_err = game.get("home", {}).get("errors", 0)

    balls, strikes, outs = game.get("balls", 0), game.get("strikes", 0), game.get("outs", 0)
    base1 = int(game.get("base1", 0) or 0) > 0
    base2 = int(game.get("base2", 0) or 0) > 0
    base3 = int(game.get("base3", 0) or 0) > 0

    def cls(bit: int, on: bool) -> str:
        if not on:
            return ""
        flash = " flash" if (changed_mask & bit) else ""
        return f"on{flash}"

    away_lineup = escape(", ".join(game.get("away_lineup_preview", [])[:3]) or "-")
    home_lineup = escape(", ".join(game.get("home_lineup_preview", [])[:3]) or "-")
    local_time = escape(str(game.get("last_update", "-")))

    gradient = matchup_gradient(away, home)

    return f"""
    <article class="card" style="background:{gradient};">
      <div class="scoreboard">
        <div class="team"><h3>{away}</h3><div class="score">{away_score}</div><div>H {away_hits} / E {away_err}</div></div>
        <div class="mid"><div>{status}</div><div>{half} {inning}</div></div>
        <div class="team"><h3>{home}</h3><div class="score">{home_score}</div><div>H {home_hits} / E {home_err}</div></div>
      </div>
      <div class="meta">
        <div class="meta-box"><div class="label">BALL</div><div class="val">{balls}</div></div>
        <div class="meta-box"><div class="label">STRIKE</div><div class="val">{strikes}</div></div>
        <div class="meta-box"><div class="label">OUT</div><div class="val">{outs}</div></div>
        <div class="meta-box diamond-wrap">
          <div class="diamond" aria-label="base-runner-state">
            <span class="base second {cls(2, base2)}"></span>
            <span class="base third {cls(4, base3)}"></span>
            <span class="base first {cls(1, base1)}"></span>
            <span class="base home"></span>
          </div>
        </div>
      </div>
      <div class="players">
        <div class="row"><span class="k">선발투수</span> | {away}: {escape(str(game.get('probable_pitcher_away','-')))} (ERA {escape(str(game.get('probable_pitcher_away_era','-')))}) / {home}: {escape(str(game.get('probable_pitcher_home','-')))} (ERA {escape(str(game.get('probable_pitcher_home_era','-')))} )</div>
        <div class="row"><span class="k">현재 타석/투수</span> | 타자: {escape(str(game.get('current_batter','-')))} ({escape(str(game.get('current_batter_position','-')))}, AVG {escape(str(game.get('current_batter_avg','-')))} ) / 투수: {escape(str(game.get('current_pitcher','-')))} ({escape(str(game.get('current_pitcher_position','-')))}, ERA {escape(str(game.get('current_pitcher_era','-')))} )</div>
        <div class="row"><span class="k">라인업 미리보기</span> | {away}: {away_lineup}</div>
        <div class="row"><span class="k">라인업 미리보기</span> | {home}: {home_lineup}</div>
      </div>
      <p class="sub">Last update: {local_time}</p>
    </article>
    """


def main() -> None:
    st.set_page_config(page_title="MLB Live Dashboard", layout="wide")
    style()
    st_autorefresh(interval=5000, key="mlb_refresh")

    if "mlb_provider" not in st.session_state:
        st.session_state.mlb_provider = MlbLiveProvider()
        run_async(st.session_state.mlb_provider.start())

    provider: MlbLiveProvider = st.session_state.mlb_provider
    games = [s.model_dump() for s in run_async(provider.refresh())]

    st.markdown("## MLB Live Dashboard")
    st.caption("Dodgers/Padres 관련 경기 중심 실시간 대시보드")

    prev = st.session_state.get("mlb_prev_mask", {})
    changed = {}
    for g in games:
        gid = g.get("game_id", f"{g['away']['name']}-{g['home']['name']}")
        cur = base_mask(g)
        changed[gid] = cur ^ prev.get(gid, 0)
        prev[gid] = cur
    st.session_state.mlb_prev_mask = prev

    if not games:
        st.info("표시할 경기가 없습니다.")
        return

    html = '<div class="cards">'
    for g in games[:2]:
        gid = g.get("game_id", f"{g['away']['name']}-{g['home']['name']}")
        html += render_game(g, changed.get(gid, 0))
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
