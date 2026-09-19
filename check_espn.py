"""
Connection check. Run this when league_dashboard.py or espn_probe.py says it
can't reach the league.

    python3 check_espn.py
    python3 check_espn.py 2024        # test one specific season

Prints what it finds and how to fix it. Never prints your cookie values,
only their length and shape.
"""

import os
import sys

import requests

try:
    import league_dashboard as ld
except Exception as e:
    print(f"Can't import league_dashboard.py: {e}")
    print("Run this from the folder that has league_dashboard.py in it.")
    sys.exit(1)

HOSTS = [
    ("lm-api-reads", "https://lm-api-reads.fantasy.espn.com"),
    ("fantasy.espn", "https://fantasy.espn.com"),
]


def shape(name, val):
    if not val:
        return f"  {name}: MISSING"
    return f"  {name}: {len(val)} chars, starts {val[:4]!r}, ends {val[-4:]!r}"


def main():
    print("=" * 64)
    print("1. Credentials")
    print("=" * 64)

    ld.load_credentials()
    src = lambda v: "environment" if v else "CONFIG block"
    print(f"  LEAGUE_ID: {ld.LEAGUE_ID or 'MISSING'}"
          f"   (from {src(os.environ.get('ESPN_LEAGUE_ID'))})")
    print(shape("SWID", ld.SWID) + f"   (from {src(os.environ.get('ESPN_SWID'))})")
    print(shape("espn_s2", ld.ESPN_S2) + f"   (from {src(os.environ.get('ESPN_S2'))})")

    problems = []
    if not ld.SWID:
        problems.append("SWID is empty")
    elif not (ld.SWID.startswith("{") and ld.SWID.endswith("}")):
        problems.append("SWID should be wrapped in curly braces, like {ABC-123...}")
    if not ld.ESPN_S2:
        problems.append("espn_s2 is empty")
    elif len(ld.ESPN_S2) < 200:
        problems.append(f"espn_s2 looks short at {len(ld.ESPN_S2)} chars — it is "
                        "normally 250+. The devtools cookie panel truncates when "
                        "you copy from the table; open the value itself first.")
    if " " in ld.ESPN_S2 or "\n" in ld.ESPN_S2:
        problems.append("espn_s2 contains whitespace — it probably got wrapped "
                        "when pasted. It should be one unbroken string.")
    for p in problems:
        print(f"  !! {p}")
    if not problems:
        print("  Both cookies look well formed.")

    seasons = [int(sys.argv[1])] if len(sys.argv) > 1 else [
        ld.CURRENT_SEASON, ld.CURRENT_SEASON - 1, ld.CURRENT_SEASON - 2]

    print()
    print("=" * 64)
    print("2. What does ESPN actually say?")
    print("=" * 64)

    cookies = {"SWID": ld.SWID, "espn_s2": ld.ESPN_S2}
    ok_any = False

    for season in seasons:
        print(f"\n  season {season}")
        for label, host in HOSTS:
            url = (f"{host}/apis/v3/games/ffl/seasons/{season}"
                   f"/segments/0/leagues/{ld.LEAGUE_ID}")
            for auth in ("with cookies", "no cookies"):
                try:
                    r = requests.get(url, params={"view": "mTeam"}, timeout=20,
                                     cookies=cookies if auth == "with cookies" else {})
                    note = ""
                    if r.status_code == 200:
                        try:
                            body = r.json()
                            body = body[0] if isinstance(body, list) and body else body
                            n = len(body.get("teams") or [])
                            note = f"-> {n} teams"
                            if n:
                                ok_any = True
                        except Exception:
                            note = "-> 200 but body wasn't league JSON"
                    print(f"    {label:<14} {auth:<12} HTTP {r.status_code} {note}")
                except Exception as e:
                    print(f"    {label:<14} {auth:<12} {type(e).__name__}: {e}")

    print()
    print("=" * 64)
    print("3. What to do")
    print("=" * 64)
    if ok_any:
        print("  A request came back with teams, so the league is reachable.")
        print("  Re-run: python3 espn_probe.py")
    elif not (ld.SWID and ld.ESPN_S2):
        print("  No cookies were sent, so every request above was anonymous and")
        print("  the 401s say nothing about whether your cookies are still good.")
        print("  Fill in the CONFIG block at the top of league_dashboard.py:")
        print('         SWID    = "{YOUR-SWID-HERE}"')
        print('         ESPN_S2 = "AEB...long value..."')
        print("  or set ESPN_SWID and ESPN_S2 in the environment, then re-run.")
    else:
        print("  Read the status codes above:")
        print()
        print("  401 or 403  Cookies are wrong or expired. Log into ESPN in a")
        print("              browser, open devtools > Application > Cookies >")
        print("              espn.com, and copy SWID and espn_s2 fresh. Click the")
        print("              value to expand it before copying — the table view")
        print("              cuts espn_s2 off. Paste both into the CONFIG block at")
        print("              the top of league_dashboard.py.")
        print()
        print("  404         Wrong league id, or that season doesn't exist for this")
        print("              league. Open the league in a browser and check the")
        print("              leagueId in the URL.")
        print()
        print("  200 with no teams")
        print("              Reached ESPN but got an empty league. Usually means the")
        print("              season predates this league. Try an earlier season:")
        print("              python3 check_espn.py 2023")
        print()
        print("  Connection errors on every host")
        print("              Network or DNS, not ESPN. Check VPN or firewall.")


if __name__ == "__main__":
    main()