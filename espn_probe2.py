"""
Probe round two. The first probe answered the big questions; this one nails
down the two loose ends:

  * transactions came back HTTP 400, so the filter shape was wrong. Try the
    variants until one works.
  * confirm the weekly projection is genuinely per-week, which decides whether
    "started a player who wasn't playing" is recoverable.

    python3 espn_probe2.py            # most recent finished season
    python3 espn_probe2.py 2024

Prints everything to the console. No cookies, no player names, no manager
names — safe to paste back.
"""

import json
import sys
from collections import Counter

import league_dashboard as ld

SLOT = {0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
        7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
        14: "DB", 15: "DP", 16: "DST", 17: "K", 18: "P", 19: "HC",
        20: "BENCH", 21: "IR", 22: "?", 23: "FLEX", 24: "EDR"}
BENCHED = {20, 21}


def url(season, path=""):
    return (f"{ld.HOST}/apis/v3/games/ffl/seasons/{season}"
            f"/segments/0/leagues/{ld.LEAGUE_ID}{path}")


def transactions(season):
    print("\n" + "=" * 60)
    print("A. Transactions — finding a shape ESPN accepts")
    print("=" * 60)

    comm_filter = {"topics": {
        "filterType": {"value": ["ACTIVITY_TRANSACTIONS"]},
        "limit": 1000, "limitPerMessageSet": {"value": 1000}, "offset": 0,
        "sortMessageDate": {"sortPriority": 1, "sortAsc": False},
        "sortFor": {"sortPriority": 2, "sortAsc": False},
        "filterIncludeMessageTypeIds": {"value": [178, 180, 179, 239, 181, 244]}}}

    attempts = [
        ("communication + kona_league_communication",
         url(season, "/communication/"), {"view": "kona_league_communication"},
         {"x-fantasy-filter": json.dumps(comm_filter)}),
        ("mTransactions2, no filter",
         url(season), {"view": "mTransactions2"}, {}),
        ("mTransactions2 + scoringPeriodId",
         url(season), {"view": "mTransactions2", "scoringPeriodId": 1}, {}),
        ("mTeam + mRoster (adds show up as acquisitionType)",
         url(season), {"view": "mTeam"}, {}),
    ]

    winner = None
    for label, u, params, headers in attempts:
        try:
            r = ld.SESSION.get(u, params=params, headers=headers, timeout=30)
            note = ""
            if r.status_code == 200:
                body = r.json()
                body = body[0] if isinstance(body, list) and body else body
                topics = body.get("topics") or []
                tx = body.get("transactions") or []
                teams = body.get("teams") or []
                if topics:
                    kinds = Counter(t.get("type") for t in topics)
                    note = f"-> {len(topics)} activity topics {dict(kinds)}"
                    winner = winner or label
                elif tx:
                    note = f"-> {len(tx)} transactions"
                    winner = winner or label
                elif teams:
                    tr = [t.get("transactionCounter") for t in teams
                          if t.get("transactionCounter")]
                    if tr:
                        k = sorted(tr[0].keys())
                        note = f"-> transactionCounter present, keys={k}"
                        winner = winner or label
                    else:
                        note = "-> 200 but no transaction data"
                else:
                    note = "-> 200 but body had nothing useful"
            print(f"  {label:<46} HTTP {r.status_code} {note}")
        except Exception as e:
            print(f"  {label:<46} {type(e).__name__}: {e}")

    print(f"\n  best option: {winner or 'NONE — waiver counts are out'}")


def lineups(season, week=5):
    print("\n" + "=" * 60)
    print(f"B. Box score structure, week {week}")
    print("=" * 60)

    r = ld.SESSION.get(url(season), timeout=30,
                       params={"view": ["mMatchupScore", "mBoxscore"],
                               "scoringPeriodId": week})
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}")
        return
    sched = [g for g in (r.json().get("schedule") or [])
             if g.get("matchupPeriodId") == week]

    slots, per_team = Counter(), Counter()
    started_zero = benched = 0
    proj_right_week = proj_any = actual_right_week = 0
    zero_proj_started = 0
    sample = None

    for g in sched:
        for side in ("home", "away"):
            s = g.get(side) or {}
            tid = s.get("teamId")
            entries = ((s.get("rosterForCurrentScoringPeriod") or {})
                       .get("entries") or [])
            per_team[tid] = len(entries)
            for e in entries:
                sid = e.get("lineupSlotId")
                slots[SLOT.get(sid, sid)] += 1
                p = ((e.get("playerPoolEntry") or {}).get("player") or {})
                stats = p.get("stats") or []
                act = next((x for x in stats if x.get("statSourceId") == 0
                            and x.get("scoringPeriodId") == week), None)
                prj = next((x for x in stats if x.get("statSourceId") == 1
                            and x.get("scoringPeriodId") == week), None)
                if prj:
                    proj_right_week += 1
                if any(x.get("statSourceId") == 1 for x in stats):
                    proj_any += 1
                if act:
                    actual_right_week += 1
                if sid in BENCHED:
                    benched += 1
                else:
                    a = (act or {}).get("appliedTotal")
                    pv = (prj or {}).get("appliedTotal")
                    if a is not None and abs(a) < 0.01:
                        started_zero += 1
                        if pv is not None and pv < 1.0:
                            zero_proj_started += 1
                if sample is None and act and prj:
                    sample = {
                        "lineupSlot": SLOT.get(sid, sid),
                        "defaultPositionId": p.get("defaultPositionId"),
                        "injuryStatus": p.get("injuryStatus"),
                        "injured": p.get("injured"),
                        "actual_appliedTotal": act.get("appliedTotal"),
                        "projected_appliedTotal": prj.get("appliedTotal"),
                        "player_keys_available": sorted(p.keys()),
                        "entry_keys_available": sorted(e.keys()),
                    }

    print(f"  matchups: {len(sched)}   teams: {len(per_team)}")
    print(f"  roster size per team: {sorted(set(per_team.values()))}")
    print(f"  slot breakdown: {dict(slots)}")
    print(f"  bench/IR entries: {benched}   (needed for optimal-lineup %)")
    print(f"  actual points tagged to week {week}: {actual_right_week}")
    print(f"  projections tagged to week {week}: {proj_right_week}   "
          f"(any week: {proj_any})")
    print(f"  starters who scored 0: {started_zero}")
    print(f"  ...of those, also projected under 1.0: {zero_proj_started}")
    print(f"\n  sample entry: {json.dumps(sample, indent=2) if sample else 'none'}")

    print("\n  verdict:")
    if benched > 0 and actual_right_week > 0:
        print("    optimal-lineup % : BUILDABLE")
    else:
        print("    optimal-lineup % : NOT buildable (no bench data)")
    if proj_right_week > 0:
        print("    'started someone who wasn't playing' : BUILDABLE via projections")
    else:
        print("    'started someone who wasn't playing' : falls back to the blunt")
        print("      zero-point count, which can't tell a bust from an absentee")


def rosters_changed(season):
    print("\n" + "=" * 60)
    print("C. League size check")
    print("=" * 60)
    r = ld.SESSION.get(url(season), params={"view": "mTeam"}, timeout=30)
    if r.status_code == 200:
        body = r.json()
        body = body[0] if isinstance(body, list) and body else body
        st = (body.get("settings") or {}).get("scheduleSettings") or {}
        print(f"  teams: {len(body.get('teams') or [])}")
        print(f"  playoffTeamCount: {st.get('playoffTeamCount')}")
        print(f"  matchupPeriodCount: {st.get('matchupPeriodCount')}")


def main():
    if not ld.load_credentials():
        return
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    print(f"Probing league {ld.LEAGUE_ID}, season {season}")
    transactions(season)
    lineups(season)
    rosters_changed(season)


if __name__ == "__main__":
    main()