"""
ESPN endpoint probe — run this before we build the rest of the Advanced view.
=============================================================================
Checks whether draft, box score and transaction data actually come back for a
COMPLETED season. Completed seasons are settled data, so these should behave
better than the live-draft endpoint that has been failing.

    python espn_probe.py            # probes the most recent finished season
    python espn_probe.py 2024       # probes a specific season

Writes probe_report.json. That file contains structure and counts only, no
cookies and no personal identifiers — safe to paste back to me.
"""

import json
import sys
from collections import Counter

import league_dashboard as ld

SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "DST", 17: "K",
        20: "BENCH", 21: "IR", 23: "FLEX"}
STARTER = lambda s: s not in (20, 21)


def get(season, views, extra=None, headers=None):
    params = [("view", v) for v in views]
    if extra:
        params += list(extra.items())
    url = (f"{ld.HOST}/apis/v3/games/ffl/seasons/{season}"
           f"/segments/0/leagues/{ld.LEAGUE_ID}")
    r = ld.SESSION.get(url, params=params, timeout=30, headers=headers or {})
    return r


def probe_draft(season, out):
    print("\n[1/4] mDraftDetail — draft slot vs finish")
    r = get(season, ["mDraftDetail"])
    if r.status_code != 200:
        out["draft"] = {"ok": False, "status": r.status_code}
        print(f"      HTTP {r.status_code}")
        return
    d = (r.json().get("draftDetail") or {})
    picks = d.get("picks") or []
    real = [p for p in picks if p.get("playerId", -1) != -1]
    by_team = Counter(p.get("teamId") for p in real)
    out["draft"] = {"ok": bool(real), "drafted": d.get("drafted"),
                    "picks": len(picks), "populated": len(real),
                    "teams_with_picks": len(by_team),
                    "has_slot_order": bool(real and real[0].get("overallPickNumber")),
                    "sample_keys": sorted(picks[0].keys()) if picks else []}
    print(f"      {len(real)} of {len(picks)} picks populated "
          f"across {len(by_team)} teams")
    if not real:
        print("      >> same failure as the live draft. Draft slot metric is out.")


def probe_boxscore(season, out):
    print("\n[2/4] mBoxscore — lineups, optimal lineup %, zero-point starts")
    r = get(season, ["mMatchupScore", "mBoxscore"], {"scoringPeriodId": 1})
    if r.status_code != 200:
        out["boxscore"] = {"ok": False, "status": r.status_code}
        print(f"      HTTP {r.status_code}")
        return
    sched = r.json().get("schedule") or []
    entries, slots, has_actual, has_proj, has_injury = 0, Counter(), 0, 0, 0
    sample = None
    for g in sched:
        for side in ("home", "away"):
            roster = ((g.get(side) or {}).get("rosterForCurrentScoringPeriod") or {})
            for e in (roster.get("entries") or []):
                entries += 1
                slots[SLOT.get(e.get("lineupSlotId"), e.get("lineupSlotId"))] += 1
                p = ((e.get("playerPoolEntry") or {}).get("player") or {})
                stats = p.get("stats") or []
                if any(s.get("statSourceId") == 0 for s in stats):
                    has_actual += 1
                if any(s.get("statSourceId") == 1 for s in stats):
                    has_proj += 1
                if p.get("injuryStatus"):
                    has_injury += 1
                if sample is None and stats:
                    sample = {"slot": SLOT.get(e.get("lineupSlotId")),
                              "injuryStatus": p.get("injuryStatus"),
                              "injured": p.get("injured"),
                              "stat_blocks": [{"statSourceId": s.get("statSourceId"),
                                               "scoringPeriodId": s.get("scoringPeriodId"),
                                               "appliedTotal": s.get("appliedTotal")}
                                              for s in stats[:4]]}
    out["boxscore"] = {"ok": entries > 0, "entries": entries,
                       "slots": dict(slots), "with_actual_points": has_actual,
                       "with_projection": has_proj,
                       "with_injury_status": has_injury, "sample": sample}
    print(f"      {entries} roster entries, {has_actual} with actual points, "
          f"{has_proj} with projections, {has_injury} with an injury field")
    if entries and has_actual:
        print("      >> optimal lineup % and zero-point starts are buildable")
    if has_proj:
        print("      >> weekly projections exist — a real 'started someone who "
              "wasn't playing' count may be recoverable")


def probe_transactions(season, out):
    print("\n[3/4] mTransactions2 — waiver activity")
    filt = {"transactions": {"filterType": {"value": ["WAIVER", "FREEAGENT", "TRADE"]}}}
    r = get(season, ["mTransactions2"],
            headers={"X-Fantasy-Filter": json.dumps(filt)})
    if r.status_code != 200:
        out["transactions"] = {"ok": False, "status": r.status_code}
        print(f"      HTTP {r.status_code}")
        return
    tx = r.json().get("transactions") or []
    kinds = Counter(t.get("type") for t in tx)
    out["transactions"] = {"ok": bool(tx), "count": len(tx),
                           "types": dict(kinds),
                           "sample_keys": sorted(tx[0].keys()) if tx else []}
    print(f"      {len(tx)} transactions: {dict(kinds)}")


def probe_weeks(season, out):
    print("\n[4/4] box score coverage across the season")
    found = []
    for w in (1, 5, 9, 13):
        r = get(season, ["mMatchupScore", "mBoxscore"], {"scoringPeriodId": w})
        n = 0
        if r.status_code == 200:
            for g in (r.json().get("schedule") or []):
                for side in ("home", "away"):
                    ros = ((g.get(side) or {}).get("rosterForCurrentScoringPeriod") or {})
                    n += len(ros.get("entries") or [])
        found.append({"week": w, "entries": n})
        print(f"      week {w}: {n} roster entries")
    out["week_coverage"] = found
    if all(f["entries"] == 0 for f in found):
        print("      >> no weekly lineups available. Lineup metrics are out.")


def main():
    if not ld.load_credentials():
        return

    if len(sys.argv) > 1:
        season = int(sys.argv[1])
    else:
        seasons = ld.discover_seasons()
        if not seasons:
            print("Could not reach the league. Check the cookies first.")
            return
        season = seasons[-2] if len(seasons) > 1 else seasons[-1]

    print(f"Probing league {ld.LEAGUE_ID}, season {season}")
    out = {"season": season}
    probe_draft(season, out)
    probe_boxscore(season, out)
    probe_transactions(season, out)
    probe_weeks(season, out)

    with open(ld.here("probe_report.json"), "w") as f:
        json.dump(out, f, indent=1)
    print("\nWrote probe_report.json — no cookies or names in it, safe to share.")


if __name__ == "__main__":
    main()