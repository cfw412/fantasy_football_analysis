"""Offline test for league_dashboard.py. Builds a fake 12-team ESPN payload for
three seasons, runs the whole pipeline, and checks the maths. No network.

    python test_dashboard.py
"""
import json
import random
import urllib.request
import threading
import time

import league_dashboard as ld

random.seed(11)

N_TEAMS, N_WEEKS, SEASONS = 12, 13, [2023, 2024, 2025]
# each team has a true scoring mean; team 1 is a juggernaut, team 12 is not
MEANS = {i: 130 - (i - 1) * 3.5 for i in range(1, N_TEAMS + 1)}


def fake_season(season):
    FIRST = ["Charlie", "Dana", "Marcus", "Priya", "Tom", "Alex",
             "Jordan", "Sam", "Nina", "Wes", "Ruby", "Owen"]
    LAST = ["Bennett", "Ortiz", "Kowalski", "Shah", "Delgado", "Frost",
            "Nakamura", "Ellis", "Vaughn", "Pierce", "Mbeki", "Quinn"]
    members = [{"id": f"{{OWNER-{i}}}", "firstName": FIRST[i - 1], "lastName": LAST[i - 1]}
               for i in range(1, N_TEAMS + 1)]
    # final playoff finish deliberately disagrees with regular-season order
    finals = [2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11]
    teams = [{"id": i, "name": f"Team {i}", "abbrev": f"T{i}",
              "owners": [f"{{OWNER-{i}}}"],
              "rankCalculatedFinal": finals[i - 1],
              "playoffSeed": 0}
             for i in range(1, N_TEAMS + 1)]

    schedule = []
    ids = list(range(1, N_TEAMS + 1))
    for w in range(1, N_WEEKS + 1):
        shuffled = ids[:]
        random.shuffle(shuffled)
        pairs = [(shuffled[k], shuffled[k + 1]) for k in range(0, N_TEAMS, 2)]
        for h, a in pairs:
            hp = round(random.gauss(MEANS[h], 22), 2)
            ap = round(random.gauss(MEANS[a], 22), 2)
            schedule.append({
                "matchupPeriodId": w, "playoffTierType": "NONE",
                "winner": "HOME" if hp > ap else "AWAY",
                "home": {"teamId": h, "totalPoints": hp},
                "away": {"teamId": a, "totalPoints": ap},
            })
    # a playoff game and an unplayed week that must both be ignored
    schedule.append({"matchupPeriodId": N_WEEKS + 1, "playoffTierType": "WINNERS_BRACKET",
                     "winner": "HOME",
                     "home": {"teamId": 1, "totalPoints": 999.0},
                     "away": {"teamId": 2, "totalPoints": 1.0}})
    schedule.append({"matchupPeriodId": N_WEEKS + 2, "playoffTierType": "NONE",
                     "winner": "UNDECIDED",
                     "home": {"teamId": 3, "totalPoints": 0},
                     "away": {"teamId": 4, "totalPoints": 0}})

    return {"seasonId": season, "members": members, "teams": teams,
            "schedule": schedule,
            "settings": {"name": "The Fake Bowl",
                         "scheduleSettings": {"playoffTeamCount": 6}},
            "status": {"previousSeasons": [s for s in SEASONS if s < season]}}


ld.fetch_season = lambda season, refresh=False: (
    fake_season(season) if season in SEASONS else None)
ld.CURRENT_SEASON = 2025

fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


print("\n--- pipeline ---")
seasons = ld.discover_seasons()
check("seasons discovered", seasons == SEASONS, str(seasons))

payload = ld.build_payload(seasons)
check("all seasons present", payload["seasons"] == [str(s) for s in SEASONS])
check("league name", payload["league_name"] == "The Fake Bowl")
check("franchise identity is stable across seasons",
      len(payload["teams"]) == N_TEAMS, f"{len(payload['teams'])} keys")

s = payload["data"]["2025"]

print("\n--- schedule filtering ---")
check("playoff + unplayed weeks excluded", s["weeks"] == list(range(1, N_WEEKS + 1)),
      str(s["weeks"]))
check("no 999-point playoff blowout leaked in",
      max(t["high"] for t in s["teams"]) < 300)

print("\n--- records ---")
total_w = sum(t["wins"] for t in s["teams"])
total_l = sum(t["losses"] for t in s["teams"])
check("wins equal losses", total_w == total_l, f"{total_w}W / {total_l}L")
check("every team played every week",
      all(t["games"] == N_WEEKS for t in s["teams"]))

print("\n--- luck maths ---")
tot_luck = sum(t["luck_wins"] for t in s["teams"])
check("luck is zero-sum across the league", abs(tot_luck) < 0.05, f"sum={tot_luck:.3f}")

tot_exp = sum(t["exp_wins"] for t in s["teams"])
check("expected wins sum to games played", abs(tot_exp - N_TEAMS * N_WEEKS / 2) < 0.05,
      f"{tot_exp:.2f} vs {N_TEAMS * N_WEEKS / 2}")

for t in s["teams"]:
    ap = sum(g["allplay_p"] for g in t["weekly"])
    if abs(ap - t["exp_wins"]) > 0.02:
        check(f"exp_wins matches weekly all-play for {t['name']}", False)
        break
else:
    check("exp_wins reconciles with the weekly all-play column", True)

check("luck index sign follows wins-above-expected",
      all((t["luck_wins"] >= 0) == (t["luck_z"] >= 0) for t in s["teams"]))
check("luck index stays in a sane range",
      all(abs(t["luck_z"]) < 5 for t in s["teams"]),
      f"max |z| = {max(abs(t['luck_z']) for t in s['teams']):.2f}")

allplay_total = sum(t["allplay_w"] for t in s["teams"])
expect_pairs = N_WEEKS * N_TEAMS * (N_TEAMS - 1) / 2
check("all-play wins sum to every pairing", abs(allplay_total - expect_pairs) < 0.5,
      f"{allplay_total} vs {expect_pairs}")

print("\n--- ranks ---")
check("ppg ranks are a clean 1..12", sorted(t["ppg_rank"] for t in s["teams"])
      == list(range(1, N_TEAMS + 1)))
check("finishes are a clean 1..12", sorted(t["finish"] for t in s["teams"])
      == list(range(1, N_TEAMS + 1)))
best = min(s["teams"], key=lambda t: t["ppg_rank"])
check("the juggernaut scores the most", best["team_name"] == "Team 1", best["name"])
check("fewest points against ranks first",
      min(s["teams"], key=lambda t: t["pa_rank"])["papg"]
      == min(t["papg"] for t in s["teams"]))

print("\n--- cross-season history ---")
line = payload["history"]["o:{OWNER-1}"]["line"]
check("history spans every season", [l["season"] for l in line] == SEASONS)
check("history carries a rank for each season", all(l["ppg_rank"] for l in line))
check("opponent gap is zero-sum",
      abs(sum(t["opp_gap"] for t in s["teams"])) < 0.05)

print("\n--- manager names and finishes ---")
names = {t["name"] for t in s["teams"]}
check("names are First-Initial format", all(len(n.split()) == 2 and len(n.split()[1]) == 1
                                            for n in names), sorted(names)[0])
check("team names kept alongside", all(t["team_name"].startswith("Team") for t in s["teams"]))
check("final playoff rank captured", sorted(t["final_rank"] for t in s["teams"])
      == list(range(1, N_TEAMS + 1)))
check("final rank differs from regular-season finish",
      any(t["final_rank"] != t["finish"] for t in s["teams"]))

print("\n--- consistency ---")
check("within-season sd present and positive", all(t["sd"] > 0 for t in s["teams"]))
sdr = sorted((t["sd"], t["sd_rank"]) for t in s["teams"])
check("sd ranks are valid competition ranks",
      all(r <= i + 1 for i, (_, r) in enumerate(sdr)) and sdr[0][1] == 1
      and len({r for _, r in sdr}) == len({round(v, 2) for v, _ in sdr}),
      f"{[r for _, r in sdr]}")
check("tied volatility shares a rank",
      all(a[1] == b[1] for a, b in zip(sdr, sdr[1:]) if abs(a[0] - b[0]) < 1e-9))
check("most consistent team has the lowest sd",
      min(s["teams"], key=lambda t: t["sd_rank"])["sd"] == min(t["sd"] for t in s["teams"]))
check("coefficient of variation is sane", all(2 < t["cv"] < 60 for t in s["teams"]))

print("\n--- playoff counterfactual ---")
check("playoff field read from settings", s["playoff_teams"] == 6)
check("six teams made it", sum(1 for t in s["teams"] if t["made_playoffs"]) == 6)
check("six teams deserved it", sum(1 for t in s["teams"] if t["allplay_playoffs"]) == 6)
gift = [t["name"] for t in s["teams"] if t["playoff_swing"] == "gifted"]
robb = [t["name"] for t in s["teams"] if t["playoff_swing"] == "robbed"]
check("gifted and robbed counts match", len(gift) == len(robb), f"{gift} vs {robb}")
check("no team is both", not any(t["made_playoffs"] and t["playoff_swing"] == "robbed"
                                 for t in s["teams"]))
check("schedule actually swung somebody's season", len(gift) > 0,
      f"gifted {gift}, robbed {robb}")

_raw = fake_season(2025)                       # seeds present -> must win over standings
for _t in _raw["teams"]:
    _t["playoffSeed"] = 0 if _t["id"] <= 6 else _t["id"] - 6
_seeded = ld.compute_season(ld.parse_season(_raw, 2025))
check("explicit playoff seeds override the standings fallback",
      {t["team_id"] for t in _seeded["teams"] if t["made_playoffs"]} == {7, 8, 9, 10, 11, 12})

print("\n--- close games and breaks ---")
check("close record never exceeds games",
      all(t["close_w"] + t["close_l"] <= t["games"] for t in s["teams"]))
check("close wins equal close losses league-wide",
      sum(t["close_w"] for t in s["teams"]) == sum(t["close_l"] for t in s["teams"]),
      f"{sum(t['close_w'] for t in s['teams'])}")
th = s["thresholds"]
def recount(t, res, cmp):
    return sum(1 for g in t["weekly"] if g["result"] == res and cmp(g["allplay_p"]))
check("gifted wins match the ledger",
      all(t["gifted"] == recount(t, "W", lambda p: p < th["gifted"]) for t in s["teams"]))
check("robbed losses match the ledger",
      all(t["robbed"] == recount(t, "L", lambda p: p > th["robbed"]) for t in s["teams"]))
check("breaks never exceed games played",
      all(t["gifted"] + t["robbed"] <= t["games"] for t in s["teams"]))
check("the league had some breaks to find",
      sum(t["gifted"] + t["robbed"] for t in s["teams"]) > 0,
      f"{sum(t['gifted'] for t in s['teams'])} gifted, "
      f"{sum(t['robbed'] for t in s['teams'])} robbed")

print("\n--- career aggregates ---")
a = payload["history"]["o:{OWNER-1}"]["agg"]
check("three seasons counted", a["seasons"] == 3)
manual = round(sum(l["ppg_rank"] for l in payload["history"]["o:{OWNER-1}"]["line"]) / 3, 2)
check("avg scoring rank is the plain mean", abs(a["avg_ppg_rank"] - manual) < 0.01,
      f"{a['avg_ppg_rank']} vs {manual}")
check("percentile runs 0-100", all(0 <= h["agg"]["avg_ppg_pct"] <= 100
                                   for h in payload["history"].values()))
check("best percentile belongs to the best avg rank",
      max(payload["history"].values(), key=lambda h: h["agg"]["avg_ppg_pct"])["agg"]["avg_ppg_rank"]
      == min(h["agg"]["avg_ppg_rank"] for h in payload["history"].values()))
check("rank volatility present", all("rank_sd" in h["agg"] for h in payload["history"].values()))
check("titles counted once per season", sum(h["agg"]["titles"] for h in payload["history"].values())
      == len(SEASONS), str(sum(h["agg"]["titles"] for h in payload["history"].values())))

print("\n--- serving ---")
srv = ld.Server(("127.0.0.1", 8899), ld.make_handler(payload))
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.4)
got = json.load(urllib.request.urlopen("http://localhost:8899/api/history"))
check("api serves the payload", got["league_name"] == "The Fake Bowl")
html = urllib.request.urlopen("http://localhost:8899/").read().decode()
check("dashboard.html is served", "Scoring rank by season" in html)
check("static build hook is present", "/*__EMBEDDED_DATA__*/null" in html)

print("\n--- sanitising the published build ---")
import re as _re
ld.SWID = "{DEADBEEF-0000-0000-0000-000000000000}"
ld.ESPN_S2 = "SECRETCOOKIEVALUE"
clean = ld.sanitize_payload(payload)
blob = json.dumps(clean)

check("member SWIDs are gone from the keys",
      not _re.search(r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}",
                     blob, _re.I))
check("keys became opaque ids", all(_re.fullmatch(r"m\d{2}", k) for k in clean["teams"]),
      ", ".join(sorted(clean["teams"])[:3]))
check("full owner names are gone", "owner" not in blob)
check("espn team names are gone", "team_name" not in blob)
check("league id is gone", "league_id" not in clean)

# the remap has to carry through every reference, or opponents stop resolving
keys = set(clean["teams"])
check("history is keyed the same way", set(clean["history"]) == keys)
season_keys = {t["key"] for sn in clean["data"].values() for t in sn["teams"]}
check("season team keys all remapped", season_keys <= keys, f"{len(season_keys)} keys")
opps = {g["opp"] for sn in clean["data"].values() for t in sn["teams"]
        for g in t["weekly"] if g.get("opp")}
check("every weekly opponent still resolves", opps <= keys, f"{len(opps)} opponents")

check("the numbers are untouched",
      clean["data"][str(SEASONS[-1])]["teams"][0]["ppg"]
      == payload["data"][str(SEASONS[-1])]["teams"][0]["ppg"])
check("sanitising does not mutate the original",
      any(k.startswith("o:{") for k in payload["teams"]))
check("ids are stable across repeat builds",
      json.dumps(ld.sanitize_payload(payload)) == blob)

check("a live cookie in the html is caught",
      ld.scan_for_credentials("<html>espn_s2=SECRETCOOKIEVALUE</html>"))
check("a member SWID in the html is caught",
      ld.scan_for_credentials("<html>o:{DEADBEEF-0000-0000-0000-000000000000}</html>"))
check("a clean page passes", not ld.scan_for_credentials("<html>nothing to see</html>"))

print("\n--- sample: 2025 luck table ---")
print(f"  {'team':<9}{'W-L':>7}{'PPG':>8}{'rk':>4}{'expW':>7}{'±W':>7}{'luck':>7}")
for t in sorted(s["teams"], key=lambda t: -t["luck_z"]):
    print(f"  {t['name']:<9}{t['wins']}-{t['losses']:<5}{t['ppg']:>8.1f}"
          f"{t['ppg_rank']:>4}{t['exp_wins']:>7.1f}{t['luck_wins']:>+7.1f}{t['luck_z']:>+7.1f}")

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))