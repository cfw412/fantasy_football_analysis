"""
Manager-skill metrics, built on the endpoints the probes confirmed.

  optimal lineup %   from mBoxscore: bench and IR come down with the starters,
                     and every player carries eligibleSlots, so the best legal
                     lineup for a week is computable exactly.
  dead starts        ESPN does not return a historical injury designation, so
                     a player who was ruled out is identified by his weekly
                     PROJECTION being ~0. Projected nothing and scored nothing
                     means he was never going to play: out, inactive, or on bye.
                     A player projected normally who scored 0 just busted, and
                     those are counted separately.
  waiver activity    team.transactionCounter carries season totals directly.
  draft slot         round one pick order from mDraftDetail.

Imported by league_dashboard.py. Nothing here runs on its own.
"""

import json
import os
from collections import defaultdict

import league_dashboard as ld

BENCH_SLOT, IR_SLOT = 20, 21
DEAD_PROJECTION = 1.0     # projected under this = wasn't going to play
DEAD_ACTUAL = 1.0         # and scored under this = confirmed he didn't

SLOT_NAME = {0: "QB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
             7: "SUPERFLEX", 16: "DST", 17: "K", 20: "BENCH", 21: "IR",
             23: "FLEX"}


# ---------- exact best-lineup solver ----------

def best_lineup(players, slot_counts):
    """Highest-scoring legal lineup.

    players: [(points, eligible_slot_ids)]
    slot_counts: {slot_id: how many of that slot start}
    Returns (total_points, [index of player used]).

    This is an assignment problem, not a greedy one. Filling the most
    restrictive slot first can strand a player who was the only body for a
    later slot, so it is solved as max-weight bipartite matching.
    """
    slots = []
    for sid, count in slot_counts.items():
        if sid in (BENCH_SLOT, IR_SLOT):
            continue
        slots.extend([sid] * int(count))
    if not slots or not players:
        return 0.0, []

    n_s, n_p = len(slots), len(players)
    # cost matrix, minimise negative points; ineligible pairs are heavily penalised
    BIG = 1e9
    cost = [[BIG] * n_p for _ in range(n_s)]
    for i, sid in enumerate(slots):
        for j, (pts, elig) in enumerate(players):
            if sid in elig:
                cost[i][j] = -pts

    assign = _hungarian(cost)
    total, used = 0.0, []
    for i, j in enumerate(assign):
        if j is not None and cost[i][j] < BIG / 2:
            total += players[j][0]
            used.append(j)
    return round(total, 2), used


def _hungarian(cost):
    """Rectangular assignment, rows <= or > cols. Returns row -> col or None.
    Jonker-Volgenant style shortest augmenting path, O(n^2 m)."""
    n = len(cost)
    m = len(cost[0])
    if n > m:                                   # pad columns so every row can try
        pad = n - m
        cost = [row + [0.0] * pad for row in cost]
        m = len(cost[0])
        padded = pad
    else:
        padded = 0

    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)          # column -> row
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta, j1 = INF, -1
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j], way[j] = cur, j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    out = [None] * n
    real_cols = m - padded
    for j in range(1, m + 1):
        if p[j] and j - 1 < real_cols:
            out[p[j] - 1] = j - 1
    return out


# ---------- fetching ----------

_CFG = {"session": None, "league_id": None}
_LAST_STATUS = {"code": None}


def configure(session, league_id):
    """Hand this module the caller's authenticated session.

    Necessary because running league_dashboard.py directly loads it as
    __main__, and this module's `import league_dashboard` then builds a SECOND
    copy with its own unauthenticated session. Passing the live session in
    removes any chance of using the wrong one.
    """
    _CFG["session"] = session
    _CFG["league_id"] = league_id


def _session():
    if _CFG["session"] is None:            # imported standalone, set ourselves up
        ld.load_credentials()
        _CFG["session"] = ld.SESSION
        _CFG["league_id"] = ld.LEAGUE_ID
    return _CFG["session"]


def _cache(season, tag):
    d = ld.here(ld.CACHE_DIR)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{season}_{tag}.json")


def fetch_cached(season, tag, params, headers=None, refresh=False):
    path = _cache(season, tag)
    if os.path.exists(path) and not refresh:
        with open(path) as f:
            return json.load(f)
    session = _session()
    url = (f"{ld.HOST}/apis/v3/games/ffl/seasons/{season}"
           f"/segments/0/leagues/{_CFG['league_id']}")
    r = session.get(url, params=params, headers=headers or {}, timeout=30)
    _LAST_STATUS["code"] = r.status_code
    if r.status_code != 200:
        return None
    data = r.json()
    if isinstance(data, list):
        data = data[0] if data else None
    if data is None:
        return None
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def lineup_slot_counts(season, refresh=False):
    """{slotId: starters} from mSettings. Returns (counts, reason_if_empty)."""
    data = fetch_cached(season, "settings",
                        [("view", "mSettings")], refresh=refresh)
    if not data:
        code = _LAST_STATUS["code"]
        hint = " — cookies were rejected" if code in (401, 403) else ""
        return None, f"mSettings returned HTTP {code}{hint}"
    settings = data.get("settings")
    if not settings:
        return None, f"no settings block (top-level keys: {sorted(data)[:6]})"
    roster = settings.get("rosterSettings")
    if not roster:
        return None, f"no rosterSettings (settings keys: {sorted(settings)[:8]})"
    counts = roster.get("lineupSlotCounts")
    if not counts:
        return None, f"no lineupSlotCounts (rosterSettings keys: {sorted(roster)[:8]})"
    return {int(k): v for k, v in counts.items() if v}, None


def infer_slot_counts(raw, week):
    """Work the roster shape out from the lineups themselves.

    Every team fields the same starting slots, so counting lineupSlotIds on a
    single week and taking the per-team maximum recovers the format exactly.
    This needs no settings endpoint, and it adapts on its own when the league
    changes shape between seasons.
    """
    if not raw:
        return None
    per_team = defaultdict(lambda: defaultdict(int))
    for g in raw.get("schedule", []):
        if g.get("matchupPeriodId") != week:
            continue
        for side in ("home", "away"):
            s = g.get(side) or {}
            tid = s.get("teamId")
            if tid is None:
                continue
            for e in ((s.get("rosterForCurrentScoringPeriod") or {})
                      .get("entries") or []):
                sid = e.get("lineupSlotId")
                if sid is not None and sid not in (BENCH_SLOT, IR_SLOT):
                    per_team[tid][sid] += 1
    if not per_team:
        return None
    out = defaultdict(int)
    for slots in per_team.values():
        for sid, c in slots.items():
            out[sid] = max(out[sid], c)
    return dict(out) or None


def draft_slots(season, refresh=False):
    """{teamId: round-one pick position}."""
    data = fetch_cached(season, "draft", [("view", "mDraftDetail")], refresh=refresh)
    if not data:
        return {}
    picks = ((data.get("draftDetail") or {}).get("picks") or [])
    out = {}
    for p in picks:
        if p.get("roundId") == 1 and p.get("teamId"):
            out[p["teamId"]] = p.get("roundPickNumber") or p.get("overallPickNumber")
    return out


def transaction_counts(season, refresh=False):
    """{teamId: {...}} straight from team.transactionCounter."""
    data = fetch_cached(season, "txcount", [("view", "mTeam")], refresh=refresh)
    if not data:
        return {}
    out = {}
    for t in data.get("teams", []):
        c = t.get("transactionCounter") or {}
        out[t["id"]] = {
            "acquisitions": c.get("acquisitions") or 0,
            "drops": c.get("drops") or 0,
            "trades": c.get("trades") or 0,
            "faab": round(c.get("acquisitionBudgetSpent") or 0, 2),
            "moves_to_ir": c.get("moveToIR") or 0,
        }
    return out


def week_rosters(season, week, refresh=False):
    return fetch_cached(season, f"box{week}",
                        [("view", "mMatchupScore"), ("view", "mBoxscore"),
                         ("scoringPeriodId", week)], refresh=refresh)


# ---------- per-week analysis ----------

def _stat(stats, source, week):
    for s in stats or []:
        if s.get("statSourceId") == source and s.get("scoringPeriodId") == week:
            return s.get("appliedTotal")
    return None


def analyse_week(raw, week, slot_counts):
    """{teamId: week metrics}. Missing stat lines count as zero, not as absent."""
    out = {}
    if not raw:
        return out
    for g in raw.get("schedule", []):
        if g.get("matchupPeriodId") != week:
            continue
        for side in ("home", "away"):
            s = g.get(side) or {}
            tid = s.get("teamId")
            entries = ((s.get("rosterForCurrentScoringPeriod") or {})
                       .get("entries") or [])
            if tid is None or not entries:
                continue

            pool, started_pts = [], 0.0
            zero_starts = dead_starts = 0
            for e in entries:
                sid = e.get("lineupSlotId")
                p = ((e.get("playerPoolEntry") or {}).get("player") or {})
                stats = p.get("stats") or []
                act = _stat(stats, 0, week)
                prj = _stat(stats, 1, week)
                pts = 0.0 if act is None else float(act)      # no stat line = 0
                elig = set(p.get("eligibleSlots") or [])

                if sid != IR_SLOT:                            # IR can't be started
                    pool.append((pts, elig))
                if sid not in (BENCH_SLOT, IR_SLOT):
                    started_pts += pts
                    if pts < DEAD_ACTUAL:
                        zero_starts += 1
                        if prj is not None and float(prj) < DEAD_PROJECTION:
                            dead_starts += 1

            optimal, _ = best_lineup(pool, slot_counts)
            out[tid] = {
                "started": round(started_pts, 2),
                "optimal": optimal,
                "left_on_bench": round(max(0.0, optimal - started_pts), 2),
                "zero_starts": zero_starts,
                "dead_starts": dead_starts,
                "busts": zero_starts - dead_starts,
            }
    return out


def season_extras(season, weeks, refresh=False, verbose=True):
    """Roll every week up into per-team season totals."""
    if not weeks:
        return None

    slot_counts, why = lineup_slot_counts(season, refresh=refresh)
    week_cache = {}
    if not slot_counts:
        # settings didn't give us the format, so read it off the lineups
        for w in weeks[:3]:
            raw = week_rosters(season, w, refresh=refresh)
            week_cache[w] = raw
            slot_counts = infer_slot_counts(raw, w)
            if slot_counts:
                break
        if slot_counts and verbose:
            shape = ", ".join(f"{SLOT_NAME.get(k, k)}x{v}"
                              for k, v in sorted(slot_counts.items()))
            print(f"  {season}: settings unavailable ({why}); "
                  f"read the format off the lineups instead — {shape}")

    if not slot_counts:
        if verbose:
            print(f"  {season}: can't determine the roster format ({why}), "
                  "skipping manager metrics")
        return None

    totals = defaultdict(lambda: {"started": 0.0, "optimal": 0.0,
                                  "left_on_bench": 0.0, "zero_starts": 0,
                                  "dead_starts": 0, "busts": 0, "weeks": 0})
    missing = []
    for w in weeks:
        raw = week_cache.get(w) or week_rosters(season, w, refresh=refresh)
        wk = analyse_week(raw, w, slot_counts)
        if not wk:
            missing.append(w)
        for tid, m in wk.items():
            t = totals[tid]
            for k in ("started", "optimal", "left_on_bench"):
                t[k] += m[k]
            for k in ("zero_starts", "dead_starts", "busts"):
                t[k] += m[k]
            t["weeks"] += 1

    if missing and verbose:
        print(f"  {season}: no lineup data for weeks {missing}")
    if not totals:
        return None

    tx = transaction_counts(season, refresh=refresh)
    slots = draft_slots(season, refresh=refresh)

    out = {}
    for tid, t in totals.items():
        eff = (t["started"] / t["optimal"] * 100) if t["optimal"] else 0.0
        out[tid] = {
            "lineup_eff": round(eff, 1),
            "started_pts": round(t["started"], 2),
            "optimal_pts": round(t["optimal"], 2),
            "left_on_bench": round(t["left_on_bench"], 2),
            "bench_per_week": round(t["left_on_bench"] / t["weeks"], 2)
                              if t["weeks"] else 0.0,
            "zero_starts": t["zero_starts"],
            "dead_starts": t["dead_starts"],
            "busts": t["busts"],
            "weeks_covered": t["weeks"],
            "draft_slot": slots.get(tid),
            **tx.get(tid, {"acquisitions": 0, "drops": 0, "trades": 0,
                           "faab": 0, "moves_to_ir": 0}),
        }
    return out