"""Offline test for league_extras.py. Builds fake box scores shaped like the
ones espn_probe2.py found in the real league, then checks the manager metrics.
No network.

    python3 test_extras.py
"""
import os
import random

import league_extras as lx

random.seed(5)

# the real 2025 shape the probe reported: QB RB WR WR TE FLEX OP DST, 5 bench, IR
SLOT_COUNTS = {0: 1, 2: 1, 4: 2, 6: 1, 23: 1, 7: 1, 16: 1, 20: 5, 21: 1}
ELIG = {"QB": [0, 7, 20, 21], "RB": [2, 23, 7, 20, 21], "WR": [4, 23, 7, 20, 21],
        "TE": [6, 23, 7, 20, 21], "DST": [16, 20, 21]}
POSID = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "DST": 16}

fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def player(pos, slot, week, actual, projected, drop_actual=False):
    stats = []
    if not drop_actual:
        stats.append({"statSourceId": 0, "scoringPeriodId": week,
                      "appliedTotal": actual})
    if projected is not None:
        stats.append({"statSourceId": 1, "scoringPeriodId": week,
                      "appliedTotal": projected})
    stats.append({"statSourceId": 0, "scoringPeriodId": week + 1,
                  "appliedTotal": 99.0})          # another week, must be ignored
    return {"lineupSlotId": slot,
            "playerPoolEntry": {"player": {
                "defaultPositionId": POSID[pos],
                "eligibleSlots": ELIG[pos],
                "stats": stats}}}


print("\n--- best lineup ---")
# bench player clearly better than the starter at the same position
entries = [
    player("QB", 0, 1, 10.0, 15.0),
    player("RB", 2, 1, 5.0, 12.0),
    player("WR", 4, 1, 8.0, 11.0),
    player("WR", 4, 1, 7.0, 10.0),
    player("TE", 6, 1, 4.0, 6.0),
    player("RB", 23, 1, 6.0, 9.0),
    player("QB", 7, 1, 12.0, 14.0),
    player("DST", 16, 1, 9.0, 7.0),
    player("RB", 20, 1, 30.0, 8.0),     # monster on the bench
    player("WR", 20, 1, 2.0, 9.0),
]
raw = {"schedule": [{"matchupPeriodId": 1,
                     "home": {"teamId": 1, "rosterForCurrentScoringPeriod":
                              {"entries": entries}},
                     "away": {"teamId": 2, "rosterForCurrentScoringPeriod":
                              {"entries": []}}}]}
res = lx.analyse_week(raw, 1, SLOT_COUNTS)[1]
started = 10 + 5 + 8 + 7 + 4 + 6 + 12 + 9
check("started points add up", abs(res["started"] - started) < 0.01, str(res["started"]))
check("optimal beats what was started", res["optimal"] > res["started"],
      f"{res['optimal']} vs {res['started']}")
check("bench monster is captured", res["left_on_bench"] > 20,
      f"left {res['left_on_bench']} on the bench")
check("other week's stats ignored", res["optimal"] < 200, str(res["optimal"]))

print("\n--- dead starts vs busts ---")
entries = [
    player("QB", 0, 1, 0.0, 0.0),       # ruled out: projected nothing, scored nothing
    player("RB", 2, 1, 0.0, 14.0),      # healthy bust: projected well, scored nothing
    player("WR", 4, 1, 0.0, 0.0, drop_actual=True),   # no stat line at all = bye
    player("WR", 4, 1, 11.0, 10.0),
    player("TE", 6, 1, 3.0, 5.0),
    player("RB", 23, 1, 6.0, 9.0),
    player("QB", 7, 1, 12.0, 14.0),
    player("DST", 16, 1, 9.0, 7.0),
    player("RB", 20, 1, 0.0, 0.0),      # benched, must not count
    player("WR", 21, 1, 0.0, 0.0),      # IR, must not count
]
raw = {"schedule": [{"matchupPeriodId": 1,
                     "home": {"teamId": 3, "rosterForCurrentScoringPeriod":
                              {"entries": entries}}, "away": None}]}
res = lx.analyse_week(raw, 1, SLOT_COUNTS)[3]
check("three starters scored nothing", res["zero_starts"] == 3, str(res["zero_starts"]))
check("two of them were never playing", res["dead_starts"] == 2, str(res["dead_starts"]))
check("the healthy one counts as a bust", res["busts"] == 1, str(res["busts"]))
check("bench and IR excluded from starter counts", res["zero_starts"] == 3)

print("\n--- IR cannot be used in the optimal lineup ---")
entries = [player("QB", 0, 1, 1.0, 5.0), player("RB", 2, 1, 1.0, 5.0),
           player("WR", 4, 1, 1.0, 5.0), player("WR", 4, 1, 1.0, 5.0),
           player("TE", 6, 1, 1.0, 5.0), player("RB", 23, 1, 1.0, 5.0),
           player("QB", 7, 1, 1.0, 5.0), player("DST", 16, 1, 1.0, 5.0),
           player("RB", 21, 1, 99.0, 5.0)]          # 99 points, stuck on IR
raw = {"schedule": [{"matchupPeriodId": 1,
                     "home": {"teamId": 4, "rosterForCurrentScoringPeriod":
                              {"entries": entries}}, "away": None}]}
res = lx.analyse_week(raw, 1, SLOT_COUNTS)[4]
check("IR player never enters the optimal lineup", res["optimal"] == 8.0,
      str(res["optimal"]))
check("perfect lineup reads as no loss", res["left_on_bench"] == 0.0)

print("\n--- a manager who set the best possible lineup ---")
entries = [player("QB", 0, 1, 20.0, 18.0), player("RB", 2, 1, 18.0, 12.0),
           player("WR", 4, 1, 17.0, 11.0), player("WR", 4, 1, 16.0, 10.0),
           player("TE", 6, 1, 15.0, 8.0), player("RB", 23, 1, 14.0, 9.0),
           player("QB", 7, 1, 13.0, 14.0), player("DST", 16, 1, 12.0, 7.0),
           player("WR", 20, 1, 1.0, 9.0), player("RB", 20, 1, 2.0, 8.0)]
raw = {"schedule": [{"matchupPeriodId": 1,
                     "home": {"teamId": 5, "rosterForCurrentScoringPeriod":
                              {"entries": entries}}, "away": None}]}
res = lx.analyse_week(raw, 1, SLOT_COUNTS)[5]
check("efficiency is exactly 100%", abs(res["started"] - res["optimal"]) < 0.01,
      f"{res['started']} of {res['optimal']}")

print("\n--- missing data degrades quietly ---")
check("no schedule returns nothing", lx.analyse_week(None, 1, SLOT_COUNTS) == {})
check("empty schedule returns nothing",
      lx.analyse_week({"schedule": []}, 1, SLOT_COUNTS) == {})
check("wrong week is skipped",
      lx.analyse_week(raw, 9, SLOT_COUNTS) == {})

print("\n--- inferring the roster format from lineups ---")
# rebuild the shape espn_probe2 saw in the real 2025 league
def team_entries():
    plan = [("QB",0),("RB",2),("WR",4),("WR",4),("TE",6),("RB",23),("QB",7),("DST",16),
            ("RB",20),("WR",20),("TE",20),("QB",20),("WR",20)]
    return [player(pos, slot, 1, 5.0, 5.0) for pos, slot in plan]

sched=[]
for m in range(6):
    sched.append({"matchupPeriodId":1,
                  "home":{"teamId":m*2+1,"rosterForCurrentScoringPeriod":{"entries":team_entries()}},
                  "away":{"teamId":m*2+2,"rosterForCurrentScoringPeriod":{"entries":team_entries()}}})
# one team also carries an IR player, as the real league did
sched[0]["home"]["rosterForCurrentScoringPeriod"]["entries"].append(
    player("RB",21,1,0.0,0.0))
inferred = lx.infer_slot_counts({"schedule":sched}, 1)
want = {0:1, 2:1, 4:2, 6:1, 23:1, 7:1, 16:1}
check("format recovered from lineups alone", inferred == want, str(inferred))
check("bench excluded", 20 not in inferred)
check("IR excluded", 21 not in inferred)
check("inferred format drives the same optimal as the real one",
      lx.analyse_week({"schedule":sched}, 1, inferred)[1]["optimal"]
      == lx.analyse_week({"schedule":sched}, 1, SLOT_COUNTS)[1]["optimal"])
check("no lineups returns nothing", lx.infer_slot_counts({"schedule":[]}, 1) is None)
check("None input returns nothing", lx.infer_slot_counts(None, 1) is None)

# a 14-team era format with no superflex, to prove it adapts per season
old_plan=[("QB",0),("RB",2),("RB",2),("WR",4),("WR",4),("TE",6),("RB",23),("DST",16),("QB",20)]
old=[{"matchupPeriodId":1,
      "home":{"teamId":1,"rosterForCurrentScoringPeriod":
              {"entries":[player(p,s,1,5.0,5.0) for p,s in old_plan]}},
      "away":None}]
check("adapts to an older, different format",
      lx.infer_slot_counts({"schedule":old}, 1) == {0:1, 2:2, 4:2, 6:1, 23:1, 16:1},
      str(lx.infer_slot_counts({"schedule":old}, 1)))

print("\n--- uses the session it was handed ---")
class FakeResponse:
    status_code = 200
    def __init__(self, payload): self._p = payload
    def json(self): return self._p

class FakeSession:
    """Stands in for the authenticated session the caller owns."""
    def __init__(self): self.calls = []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        return FakeResponse({"settings": {"rosterSettings":
                             {"lineupSlotCounts": {"0": 1, "2": 2, "20": 5}}}})

import shutil, tempfile
tmp = tempfile.mkdtemp()
_real_here, _real_dir = lx.ld.here, lx.ld.CACHE_DIR
lx.ld.here = lambda f: os.path.join(tmp, f)

fake = FakeSession()
lx.configure(fake, "999999")
counts, why = lx.lineup_slot_counts(2022, refresh=True)
check("the handed-in session is the one used", len(fake.calls) == 1, str(len(fake.calls)))
check("league id comes from configure, not the module",
      "999999" in fake.calls[0]["url"], fake.calls[0]["url"].split("/")[-1])
check("season lands in the url", "/seasons/2022/" in fake.calls[0]["url"])
check("slot counts parsed", counts == {0: 1, 2: 2, 20: 5}, str(counts))
check("no reason reported on success", why is None)

class FailSession(FakeSession):
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        r = FakeResponse(None); r.status_code = 401; return r

lx.configure(FailSession(), "999999")
counts, why = lx.lineup_slot_counts(2021, refresh=True)
check("401 is reported, not swallowed", counts is None and "401" in why, str(why))
check("401 message names the cause", "cookies" in (why or ""), str(why))

lx.ld.here, lx.ld.CACHE_DIR = _real_here, _real_dir
lx._CFG["session"] = None
shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))