"""
ESPN Fantasy League History Dashboard
=====================================
Pulls every season your league has played, computes weekly scoring, points-per-game
ranks across seasons, and a luck index, then serves a local dashboard.

    pip install requests
    # keep dashboard.html in the same folder
    python league_dashboard.py

Opens http://localhost:8766. Raw season pulls are cached in history_cache/ so
re-runs are instant; use --refresh to re-download.

    python league_dashboard.py --refresh          re-download everything
    python league_dashboard.py --season 2024      re-download one season
    python league_dashboard.py --build-static     write a single self-contained HTML
    python league_dashboard.py --no-serve         just build the cache + JSON

--------------------------------------------------------------------------------
THE LUCK INDEX
--------------------------------------------------------------------------------
You control what you score. You do not control who you are scheduled against or
what they score that week. So "luck" is the gap between the record your scoring
earned and the record you actually got.

For each week, compute the all-play win probability: the share of the OTHER teams
in the league you outscored that week (ties count half).

    p_w = (teams you beat + 0.5 * teams you tied) / (N - 1)

That is exactly your chance of winning if your opponent had been drawn at random
from the league. Summed over the season it gives expected wins:

    E = sum(p_w)

Each week is a Bernoulli trial with probability p_w, so the variance of that
expectation is sum(p_w * (1 - p_w)). The luck index is the standardized gap:

    luck_wins = actual_wins - E
    LUCK INDEX = luck_wins / sqrt(sum(p_w * (1 - p_w)))

It is a z-score, so it is comparable across seasons of different lengths and
across leagues of different sizes. Roughly:

    > +2.0   won a lot more than the scoring deserved (very fortunate)
    +0.5..2  fortunate
    -0.5..+0.5   record matches the scoring
    < -2.0   scored like a contender, finished like a doormat

Two supporting numbers are reported alongside it:

  * Opponent scoring gap: league average points-against per game minus YOUR
    points-against per game. Positive means you drew weak opposing weeks.
  * All-play record: your record if you had played every team every week. This
    is the schedule-free view of how good the team actually was.
"""

import argparse
import http.server
import json
import math
import os
import re
import socketserver
import statistics
import threading
import webbrowser
from collections import defaultdict

import requests

# ============ CONFIG ============
LEAGUE_ID = "461682"

# Cookies deliberately live nowhere near this file. Put them in
# espn_credentials.env next to it (gitignored), in the form:
#
#     ESPN_SWID={00000000-0000-0000-0000-000000000000}   <- keep the curly braces
#     ESPN_S2=<the long url-encoded blob from your browser cookies>
#
# Environment variables of the same names win over that file, which is what
# you'd use on a server: ESPN_LEAGUE_ID, ESPN_SWID, ESPN_S2.
SWID = ""
ESPN_S2 = ""

CURRENT_SEASON = 2026
EARLIEST_SEASON = 2018      # ESPN changed API generation here; older years 404
WEB_PORT = 8766
OPEN_BROWSER = True

CACHE_DIR = "history_cache"
UI_FILE = "dashboard.html"
DATA_FILE = "league_history.json"
CRED_FILE = "espn_credentials.env"   # gitignored; never commit this
STATIC_FILE = "dashboard_static.html"
# ================================

HOST = "https://lm-api-reads.fantasy.espn.com"


def here(fname):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)


def load_cred_file():
    """KEY=value lines from espn_credentials.env, if that file exists."""
    path = here(CRED_FILE)
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_credentials():
    """Environment variables first, then espn_credentials.env, then CONFIG."""
    global SWID, ESPN_S2, LEAGUE_ID
    fromfile = load_cred_file()
    pick = lambda key, current: os.environ.get(key) or fromfile.get(key) or current
    LEAGUE_ID = pick("ESPN_LEAGUE_ID", LEAGUE_ID)
    SWID = pick("ESPN_SWID", SWID)
    ESPN_S2 = pick("ESPN_S2", ESPN_S2)

    if not (SWID and ESPN_S2):
        print(f"No ESPN cookies. Create {CRED_FILE} next to this script with:\n"
              f"    ESPN_SWID={{...}}\n"
              f"    ESPN_S2=...\n"
              f"or export ESPN_SWID / ESPN_S2. To check them: python3 check_espn.py")
        return False
    if not (SWID.startswith("{") and SWID.endswith("}")):
        print("Warning: SWID is normally wrapped in curly braces, like {ABC-123...}")
    SESSION.cookies.update({"SWID": SWID, "espn_s2": ESPN_S2})
    return True


SESSION = requests.Session()


# ---------- fetching ----------

def cache_path(season):
    d = here(CACHE_DIR)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{season}.json")


def fetch_season(season, refresh=False):
    """Raw league JSON for one season. Cached to disk."""
    path = cache_path(season)
    if os.path.exists(path) and not refresh:
        with open(path) as f:
            return json.load(f)

    views = ["mMatchupScore", "mTeam", "mSettings"]
    modern = f"{HOST}/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{LEAGUE_ID}"
    legacy = f"{HOST}/apis/v3/games/ffl/leagues/{LEAGUE_ID}"

    attempts = [(modern, [("view", v) for v in views]),
                (legacy, [("seasonId", season)] + [("view", v) for v in views])]

    last = None
    for url, params in attempts:
        try:
            r = SESSION.get(url, params=params, timeout=30)
            if r.status_code != 200:
                last = ("HTTP 401/403 — cookies rejected" if r.status_code in (401, 403)
                        else f"HTTP {r.status_code}")
                continue
            data = r.json()
            if isinstance(data, list):          # historical endpoint wraps in a list
                if not data:
                    last = "empty list"
                    continue
                data = data[0]
            if not data.get("teams"):
                last = "no teams in payload"
                continue
            with open(path, "w") as f:
                json.dump(data, f)
            print(f"  {season}: pulled and cached")
            return data
        except Exception as e:
            last = f"{type(e).__name__}: {e}"

    print(f"  {season}: skipped ({last})")
    return None


def discover_seasons():
    """Ask ESPN which seasons this league has played."""
    for probe in (CURRENT_SEASON, CURRENT_SEASON - 1, CURRENT_SEASON - 2):
        raw = fetch_season(probe)
        if raw:
            prev = raw.get("status", {}).get("previousSeasons") or []
            seasons = sorted({int(s) for s in prev} | {int(raw.get("seasonId", probe))})
            old = [s for s in seasons if s < EARLIEST_SEASON]
            if old:
                print(f"Ignoring {old[0]}-{old[-1]}: ESPN's older API generation, "
                      "not reachable through this endpoint.")
            return [s for s in seasons if s >= EARLIEST_SEASON]
    print("None of the probe seasons returned a league. "
          "Run: python3 check_espn.py  for a breakdown of why.")
    return []


# ---------- parsing ----------

def team_name(t):
    name = (t.get("name") or "").strip()
    if not name:
        name = f"{t.get('location', '')} {t.get('nickname', '')}".strip()
    return name or f"Team {t.get('id')}"


def owner_name(t, members_by_id):
    for oid in (t.get("owners") or []):
        m = members_by_id.get(oid)
        if m:
            first = (m.get("firstName") or "").strip()
            last = (m.get("lastName") or "").strip()
            full = f"{first} {last}".strip()
            if full:
                return full
            if m.get("displayName"):
                return m["displayName"]
    return ""


def short_manager(full, fallback=""):
    """'Charlie Bennett' -> 'Charlie B'. Falls back to the team name."""
    parts = [p for p in (full or "").split() if p]
    if not parts:
        return fallback
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}"


def team_key(t):
    """Stable identity across seasons: owner id if we have one, else the team id."""
    owners = t.get("owners") or []
    if owners:
        return "o:" + str(owners[0])
    return "t:" + str(t.get("id"))


def side_points(side):
    if side is None:
        return None
    pts = side.get("totalPoints")
    if pts is None:
        by_period = side.get("pointsByScoringPeriod") or {}
        pts = sum(by_period.values()) if by_period else None
    return pts


def parse_season(raw, season):
    """Raw ESPN payload -> {teams, scores[team][week], opponents[team][week]}."""
    members_by_id = {m.get("id"): m for m in (raw.get("members") or [])}

    teams = {}
    for t in raw.get("teams", []):
        tid = t.get("id")
        owner = owner_name(t, members_by_id)
        tname = team_name(t)
        final = t.get("rankCalculatedFinal") or 0
        teams[tid] = {"team_id": tid,
                      "key": team_key(t),
                      "team_name": tname,
                      "name": short_manager(owner, tname),
                      "abbrev": (t.get("abbrev") or "").strip(),
                      "owner": owner,
                      "final_rank": final or None,
                      "playoff_seed": t.get("playoffSeed") or None}

    sched_settings = (raw.get("settings") or {}).get("scheduleSettings") or {}
    playoff_teams = sched_settings.get("playoffTeamCount") or 0

    scores = defaultdict(dict)
    opponents = defaultdict(dict)

    for g in raw.get("schedule", []):
        tier = g.get("playoffTierType", "NONE")
        if tier not in ("NONE", None, ""):
            continue                                  # regular season only
        week = g.get("matchupPeriodId")
        home, away = g.get("home"), g.get("away")
        if home is None or away is None:
            continue                                  # bye week in an odd league
        hp, ap = side_points(home), side_points(away)
        if hp is None or ap is None:
            continue
        if g.get("winner") in (None, "UNDECIDED") and hp == 0 and ap == 0:
            continue                                  # week not played yet
        h, a = home.get("teamId"), away.get("teamId")
        if h not in teams or a not in teams:
            continue
        scores[h][week] = round(float(hp), 2)
        scores[a][week] = round(float(ap), 2)
        opponents[h][week] = a
        opponents[a][week] = h

    return {"teams": teams, "scores": scores, "opponents": opponents,
            "season": season, "playoff_teams": playoff_teams,
            "league_name": (raw.get("settings") or {}).get("name") or "League"}


# ---------- metrics ----------

def rank_desc(values_by_key):
    """1 = highest value. Ties share the better rank."""
    order = sorted(values_by_key.items(), key=lambda kv: -kv[1])
    ranks, prev_val, prev_rank = {}, None, 0
    for i, (k, v) in enumerate(order, 1):
        if prev_val is not None and abs(v - prev_val) < 1e-9:
            ranks[k] = prev_rank
        else:
            ranks[k] = i
            prev_rank, prev_val = i, v
    return ranks


CLOSE_MARGIN = 10.0      # a game decided by less than this is a coin flip
GIFTED_P = 0.35          # won a week your score would usually have lost
ROBBED_P = 0.65          # lost a week your score would usually have won


def rank_asc(values_by_key):
    """1 = lowest value. Used where small is good, like scoring volatility."""
    return rank_desc({k: -v for k, v in values_by_key.items()})


def compute_season(parsed):
    """Everything the dashboard needs for one season."""
    teams, scores, opponents = parsed["teams"], parsed["scores"], parsed["opponents"]
    weeks = sorted({w for d in scores.values() for w in d})
    if not weeks:
        return None

    # per-week: league scoring context and all-play probabilities
    week_scores = {w: {tid: scores[tid][w] for tid in scores if w in scores[tid]}
                   for w in weeks}
    league_avg = {w: round(statistics.fmean(week_scores[w].values()), 2) for w in weeks}
    league_hi = {w: max(week_scores[w].values()) for w in weeks}
    league_lo = {w: min(week_scores[w].values()) for w in weeks}
    week_rank = {w: rank_desc(week_scores[w]) for w in weeks}

    allplay_p = defaultdict(dict)
    allplay_beat = defaultdict(float)
    allplay_games = defaultdict(int)
    for w in weeks:
        vals = week_scores[w]
        n = len(vals)
        if n < 2:
            continue
        for tid, pts in vals.items():
            beat = sum(1 for o, p in vals.items() if o != tid and p < pts)
            tied = sum(1 for o, p in vals.items() if o != tid and abs(p - pts) < 1e-9)
            p = (beat + 0.5 * tied) / (n - 1)
            allplay_p[tid][w] = p
            allplay_beat[tid] += beat + 0.5 * tied
            allplay_games[tid] += n - 1

    rows = {}
    for tid, meta in teams.items():
        my = scores.get(tid, {})
        if not my:
            continue
        played = sorted(my)
        wins = losses = ties = 0
        close_w = close_l = gifted = robbed = 0
        weekly = []
        pf = pa = 0.0
        for w in played:
            mine = my[w]
            opp_id = opponents[tid].get(w)
            opp_pts = scores.get(opp_id, {}).get(w) if opp_id else None
            pf += mine
            if opp_pts is not None:
                pa += opp_pts
                if mine > opp_pts:
                    wins += 1
                    result = "W"
                elif mine < opp_pts:
                    losses += 1
                    result = "L"
                else:
                    ties += 1
                    result = "T"
                if abs(mine - opp_pts) < CLOSE_MARGIN:
                    if result == "W":
                        close_w += 1
                    elif result == "L":
                        close_l += 1
                p = allplay_p[tid].get(w, 0.5)
                if result == "W" and p < GIFTED_P:
                    gifted += 1
                elif result == "L" and p > ROBBED_P:
                    robbed += 1
            else:
                result = "-"
            weekly.append({
                "week": w,
                "pf": round(mine, 2),
                "pa": round(opp_pts, 2) if opp_pts is not None else None,
                "opp": teams.get(opp_id, {}).get("key") if opp_id else None,
                "opp_name": teams.get(opp_id, {}).get("name") if opp_id else None,
                "result": result,
                "allplay_p": round(allplay_p[tid].get(w, 0.0), 4),
                "week_rank": week_rank[w].get(tid),
                "league_avg": league_avg[w],
            })

        g = len(played)
        actual_w = wins + 0.5 * ties
        ps = [allplay_p[tid][w] for w in played if w in allplay_p[tid]]
        exp_w = sum(ps)
        var = sum(p * (1 - p) for p in ps)
        luck_wins = actual_w - exp_w
        luck_z = luck_wins / math.sqrt(var) if var > 1e-9 else 0.0

        sd = statistics.pstdev(list(my.values())) if g > 1 else 0.0
        rows[tid] = {
            "team_id": tid, "key": meta["key"], "name": meta["name"],
            "team_name": meta["team_name"], "abbrev": meta["abbrev"],
            "owner": meta["owner"], "final_rank": meta["final_rank"],
            "playoff_seed": meta["playoff_seed"],
            "close_w": close_w, "close_l": close_l,
            "gifted": gifted, "robbed": robbed,
            "cv": round(100 * sd / (pf / g), 1) if pf else 0.0,
            "wins": wins, "losses": losses, "ties": ties, "games": g,
            "pf": round(pf, 2), "pa": round(pa, 2),
            "ppg": round(pf / g, 2), "papg": round(pa / g, 2),
            "high": round(max(my.values()), 2), "low": round(min(my.values()), 2),
            "sd": round(sd, 2),
            "exp_wins": round(exp_w, 2),
            "luck_wins": round(luck_wins, 2),
            "luck_z": round(luck_z, 2),
            "allplay_w": round(allplay_beat[tid], 1),
            "allplay_l": round(allplay_games[tid] - allplay_beat[tid], 1),
            "allplay_pct": round(allplay_beat[tid] / allplay_games[tid], 4)
                           if allplay_games[tid] else 0.0,
            "weekly": weekly,
        }

    # league-relative ranks
    for field, key in (("ppg", "ppg_rank"), ("papg", "pa_rank"),
                       ("allplay_pct", "allplay_rank")):
        ranks = rank_desc({tid: r[field] for tid, r in rows.items()})
        for tid, r in rows.items():
            r[key] = ranks[tid]
    # fewest points against is the lucky end, so invert that one
    n = len(rows)
    for r in rows.values():
        r["pa_rank"] = n + 1 - r["pa_rank"]

    finish = rank_desc({tid: (r["wins"] + 0.5 * r["ties"]) * 10000 + r["pf"]
                        for tid, r in rows.items()})
    for tid, r in rows.items():
        r["finish"] = finish[tid]

    sd_ranks = rank_asc({tid: r["sd"] for tid, r in rows.items()})
    for tid, r in rows.items():
        r["sd_rank"] = sd_ranks[tid]

    league_papg = statistics.fmean([r["papg"] for r in rows.values()])
    for r in rows.values():
        r["opp_gap"] = round(league_papg - r["papg"], 2)

    # who actually made the playoffs, and who would have on all-play alone
    n_teams = len(rows)
    field = parsed.get("playoff_teams") or 0
    if not 0 < field < n_teams:
        field = max(2, n_teams // 2)

    seeded = [r for r in rows.values() if r.get("playoff_seed")]
    if len(seeded) >= field:
        made = {r["key"] for r in rows.values()
                if r.get("playoff_seed") and r["playoff_seed"] <= field}
    else:
        made = {r["key"] for r in sorted(rows.values(), key=lambda r: r["finish"])[:field]}

    deserved = {r["key"] for r in
                sorted(rows.values(), key=lambda r: -r["allplay_pct"])[:field]}
    for r in rows.values():
        r["made_playoffs"] = r["key"] in made
        r["allplay_playoffs"] = r["key"] in deserved
        r["playoff_swing"] = ("gifted" if r["made_playoffs"] and not r["allplay_playoffs"]
                              else "robbed" if r["allplay_playoffs"] and not r["made_playoffs"]
                              else "")

    # percentile position for scoring rank: 100 = best in the league that year
    for r in rows.values():
        r["ppg_pct"] = round(100 * (n_teams - r["ppg_rank"]) / (n_teams - 1), 1) \
            if n_teams > 1 else 100.0

    return {
        "season": parsed["season"],
        "weeks": weeks,
        "n_teams": n_teams,
        "playoff_teams": field,
        "thresholds": {"close": CLOSE_MARGIN, "gifted": GIFTED_P, "robbed": ROBBED_P},
        "teams": sorted(rows.values(), key=lambda r: r["finish"]),
        "league_avg_by_week": [league_avg[w] for w in weeks],
        "league_high_by_week": [round(league_hi[w], 2) for w in weeks],
        "league_low_by_week": [round(league_lo[w], 2) for w in weeks],
        "league_ppg": round(statistics.fmean([r["ppg"] for r in rows.values()]), 2),
    }


def build_payload(seasons, refresh=False, only=None, extras=True):
    out, names, league_name = {}, {}, "League"
    for s in seasons:
        raw = fetch_season(s, refresh=refresh or (only == s))
        if not raw:
            continue
        parsed = parse_season(raw, s)
        league_name = parsed["league_name"] or league_name
        season_data = compute_season(parsed)
        if not season_data:
            print(f"  {s}: no completed regular-season games, skipped")
            continue

        if extras:
            try:
                import league_extras as lx
                lx.configure(SESSION, LEAGUE_ID)     # use THIS module's session
                ex = lx.season_extras(s, season_data["weeks"],
                                      refresh=refresh or (only == s))
                if ex:
                    hit = 0
                    for r in season_data["teams"]:
                        m = ex.get(r["team_id"])
                        if m:
                            r.update(m)
                            hit += 1
                    season_data["has_extras"] = hit > 0
                    print(f"  {s}: manager metrics for {hit} teams")
                else:
                    season_data["has_extras"] = False
            except Exception as e:
                print(f"  {s}: manager metrics failed ({type(e).__name__}: {e})")
                season_data["has_extras"] = False
        else:
            season_data["has_extras"] = False

        # league-relative ranks for the manager metrics
        if season_data.get("has_extras"):
            eff = {r["key"]: r.get("lineup_eff", 0) for r in season_data["teams"]}
            ranks = rank_desc(eff)
            for r in season_data["teams"]:
                r["lineup_eff_rank"] = ranks[r["key"]]

        out[str(s)] = season_data
        for r in season_data["teams"]:
            names[r["key"]] = {"name": r["name"], "owner": r["owner"],
                               "team_name": r["team_name"], "abbrev": r["abbrev"]}

    # cross-season: PPG rank per team per season, keyed by stable identity
    history = {}
    for key, meta in names.items():
        line = []
        for s in sorted(out, key=int):
            m = next((r for r in out[s]["teams"] if r["key"] == key), None)
            line.append({
                "season": int(s),
                "ppg_rank": m["ppg_rank"] if m else None,
                "ppg_pct": m["ppg_pct"] if m else None,
                "ppg": m["ppg"] if m else None,
                "sd": m["sd"] if m else None,
                "cv": m["cv"] if m else None,
                "finish": m["finish"] if m else None,
                "final_rank": m["final_rank"] if m else None,
                "made_playoffs": m["made_playoffs"] if m else None,
                "playoff_swing": m["playoff_swing"] if m else None,
                "luck_z": m["luck_z"] if m else None,
                "allplay_pct": m["allplay_pct"] if m else None,
                "n_teams": m and out[s]["n_teams"],
                "name": m["name"] if m else None,
                "team_name": m["team_name"] if m else None,
                "lineup_eff": m.get("lineup_eff") if m else None,
                "dead_starts": m.get("dead_starts") if m else None,
                "acquisitions": m.get("acquisitions") if m else None,
                "draft_slot": m.get("draft_slot") if m else None,
            })

        played = [l for l in line if l["ppg_rank"]]
        agg = {"seasons": len(played)}
        if played:
            ranks = [l["ppg_rank"] for l in played]
            agg.update({
                "avg_ppg_rank": round(statistics.fmean(ranks), 2),
                "avg_ppg_pct": round(statistics.fmean([l["ppg_pct"] for l in played]), 1),
                "rank_sd": round(statistics.pstdev(ranks), 2) if len(ranks) > 1 else 0.0,
                "best_rank": min(ranks),
                "worst_rank": max(ranks),
                "avg_sd": round(statistics.fmean([l["sd"] for l in played]), 2),
                "avg_cv": round(statistics.fmean([l["cv"] for l in played]), 1),
                "avg_ppg": round(statistics.fmean([l["ppg"] for l in played]), 2),
                "avg_luck": round(statistics.fmean([l["luck_z"] for l in played]), 2),
                "avg_allplay": round(statistics.fmean([l["allplay_pct"] for l in played]), 4),
                "playoff_apps": sum(1 for l in played if l["made_playoffs"]),
                "titles": sum(1 for l in played if l["final_rank"] == 1),
                "avg_final": round(statistics.fmean(
                    [l["final_rank"] for l in played if l["final_rank"]]), 2)
                    if any(l["final_rank"] for l in played) else None,
            })
            withx = [l for l in played if l.get("lineup_eff") is not None]
            if withx:
                agg.update({
                    "avg_lineup_eff": round(statistics.fmean(
                        [l["lineup_eff"] for l in withx]), 1),
                    "total_dead_starts": sum(l["dead_starts"] or 0 for l in withx),
                    "avg_acquisitions": round(statistics.fmean(
                        [l["acquisitions"] or 0 for l in withx]), 1),
                    "extras_seasons": len(withx),
                })
        history[key] = {"meta": meta, "line": line, "agg": agg}

    return {
        "league_name": league_name,
        "league_id": LEAGUE_ID,
        "seasons": sorted(out, key=int),
        "teams": names,
        "history": history,
        "data": out,
    }


# ---------- serving ----------

def make_handler(payload):
    body_json = json.dumps(payload).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/history"):
                self._send(body_json, "application/json")
                return
            try:
                with open(here(UI_FILE), "rb") as f:
                    self._send(f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self.send_error(404, f"{UI_FILE} not found next to league_dashboard.py")

    return Handler


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def sanitize_payload(payload):
    """Strip anything personal before the data is baked into a public file.

    ESPN keys every franchise by its member SWID, so the raw payload carries all
    14 members' SWIDs — including the one that is half of your own login cookie.
    Those keys are remapped to opaque ids, and the full legal names and ESPN team
    names are dropped, since the dashboard only ever shows "First L".
    """
    out = json.loads(json.dumps(payload))          # never mutate the caller's copy

    # sorted so a rebuild produces the same ids, keeping colours stable
    order = sorted(out.get("teams", {}).keys())
    mapping = {k: f"m{i:02d}" for i, k in enumerate(order, 1)}

    # a whole-document string swap catches every reference (team keys, weekly
    # opponents, history keys) without having to know where they all are
    blob = json.dumps(out)
    for old, new in mapping.items():
        blob = blob.replace(json.dumps(old), json.dumps(new))
    out = json.loads(blob)

    # a recursive strip, because these fields turn up in more places than is
    # obvious: the teams map, each season's teams, history meta, and history lines
    PERSONAL = ("owner", "team_name")
    def strip(node):
        if isinstance(node, dict):
            for f in PERSONAL:
                node.pop(f, None)
            for v in node.values():
                strip(v)
        elif isinstance(node, list):
            for v in node:
                strip(v)
    strip(out)
    out.pop("league_id", None)

    return out


def scan_for_credentials(text):
    """What must never reach a file that gets published. Returns a list of findings."""
    found = []
    for label, value in (("SWID", SWID), ("espn_s2", ESPN_S2)):
        if value and value in text:
            found.append(f"the live {label} value")
    if re.search(r"espn_s2", text, re.I):
        found.append("the string 'espn_s2'")
    if re.search(r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}",
                 text, re.I):
        found.append("an ESPN member SWID")
    return found


def build_static(payload, out=None):
    """One self-contained HTML file with the data baked in and no cookies."""
    with open(here(UI_FILE)) as f:
        html = f.read()
    blob = json.dumps(sanitize_payload(payload)).replace("</", "<\\/")
    html = html.replace("/*__EMBEDDED_DATA__*/null",
                        blob, 1)

    leaks = scan_for_credentials(html)
    if leaks:
        print("REFUSING to write the static build: it contains " + ", ".join(leaks) + ".")
        print("Nothing was written. This file was about to be published publicly.")
        return None

    path = here(out or STATIC_FILE)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(html)
    kb = os.path.getsize(path) / 1024
    print(f"Wrote self-contained dashboard: {path}  ({kb:.0f} KB, no credentials)")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download every season")
    ap.add_argument("--season", type=int, help="re-download just this season")
    ap.add_argument("--no-serve", action="store_true")
    ap.add_argument("--build-static", action="store_true")
    ap.add_argument("--no-extras", action="store_true",
                    help="skip lineup/waiver/draft metrics (much faster)")
    ap.add_argument("--static-only", action="store_true",
                    help="rebuild the static file from league_history.json; "
                         "no cookies, no network")
    ap.add_argument("--out", help="where --build-static writes (e.g. docs/index.html)")
    ap.add_argument("--port", type=int, default=WEB_PORT)
    args = ap.parse_args()

    # publishing shouldn't need credentials in the room at all
    if args.static_only:
        path = here(DATA_FILE)
        if not os.path.exists(path):
            print(f"No {DATA_FILE} yet — run a normal build once so there is data.")
            return
        with open(path) as f:
            payload = json.load(f)
        print(f"Rebuilding from {DATA_FILE}: {len(payload['seasons'])} seasons")
        build_static(payload, args.out)
        return

    if not load_credentials():
        return

    print(f"League {LEAGUE_ID} — finding seasons...")
    seasons = discover_seasons()
    if not seasons:
        print("Could not reach the league. Check LEAGUE_ID, SWID and espn_s2.")
        return
    print(f"Seasons: {', '.join(str(s) for s in seasons)}")

    payload = build_payload(seasons, refresh=args.refresh, only=args.season,
                            extras=not args.no_extras)
    if not payload["seasons"]:
        print("No season had completed games. Nothing to show.")
        return

    with open(here(DATA_FILE), "w") as f:
        json.dump(payload, f, indent=1)
    print(f"Wrote {DATA_FILE} — {len(payload['seasons'])} seasons, "
          f"{len(payload['teams'])} franchises")

    if args.build_static:
        build_static(payload, args.out)

    if args.no_serve:
        return

    srv = Server(("127.0.0.1", args.port), make_handler(payload))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://localhost:{args.port}"
    print(f"\nDashboard: {url}\nCtrl+C to stop.")
    if OPEN_BROWSER:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()