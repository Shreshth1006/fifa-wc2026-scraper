"""
FIFA World Cup 2026 – Fixture + Short Name + Results Updater → Supabase
=========================================================================
Updates ONLY these three columns, matched by 'Match Number':
    - Fixture     (e.g. "TBD vs TBD" → "Mexico vs Argentina" once decided)
    - Short Name  (e.g. "1A VS 2B"   → "MEX VS ARG" — must move together
                   with Fixture, otherwise the two go out of sync and the
                   web page shows mismatched team names)
    - Results     (e.g. "" → "2 - 1" once the match is played)

NEVER touches: Date, Group, Kick-off Time (IST)
  → These are set once when the schedule is first known and don't need
    re-scraping. Re-touching Kick-off Time was the original bug (FIFA
    stops showing a time once a match is played/in progress, so blindly
    re-scraping it wipes the existing value).

Match identity: 'Match Number' — the stable row-order identifier that
doesn't change even when Fixture/Short Name text changes (e.g. once
Round of 32 matchups are decided).

Source  : https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures
Output  : Supabase table → FIFA World Cup Schedule - Live

Requirements
------------
    pip install playwright supabase python-dotenv
    playwright install chromium

Run
---
    python SCHEDULE.py
"""

import time
import re
import os
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from supabase import create_client, Client
from dotenv import load_dotenv

# ── CONFIG ────────────────────────────────────────────────────────────────────
load_dotenv()

URL = (
    "https://www.fifa.com/en/tournaments/mens/worldcup/"
    "canadamexicousa2026/scores-fixtures?country=&wtw-filter=ALL"
)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TABLE_NAME   = "FIFA World Cup Schedule - Live"

SCROLL_PAUSE = 2.5
MAX_SCROLLS  = 80
# ─────────────────────────────────────────────────────────────────────────────


# ── HELPERS ──────────────────────────────────────────────────────────────────
def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def make_short_name(team1: str, team2: str) -> str:
    """Generate short name like 'MEX VS RSA' from full team names."""
    abbr = {
        "Mexico": "MEX", "South Africa": "RSA", "Korea Republic": "KOR",
        "Czechia": "CZE", "Canada": "CAN", "Bosnia and Herzegovina": "BIH",
        "Qatar": "QAT", "Switzerland": "SUI", "Brazil": "BRA",
        "Morocco": "MAR", "Haiti": "HAI", "Scotland": "SCO",
        "USA": "USA", "Paraguay": "PAR", "Australia": "AUS",
        "Türkiye": "TUR", "Germany": "GER", "Curaçao": "CUW",
        "Netherlands": "NED", "Japan": "JPN", "Côte d'Ivoire": "CIV",
        "Ecuador": "ECU", "Sweden": "SWE", "Tunisia": "TUN",
        "Spain": "ESP", "Cabo Verde": "CPV", "Belgium": "BEL",
        "Egypt": "EGY", "Saudi Arabia": "KSA", "Uruguay": "URU",
        "IR Iran": "IRN", "New Zealand": "NZL", "France": "FRA",
        "Senegal": "SEN", "Iraq": "IRQ", "Norway": "NOR",
        "Argentina": "ARG", "Algeria": "ALG", "Austria": "AUT",
        "Jordan": "JOR", "Portugal": "POR", "Congo DR": "COD",
        "England": "ENG", "Croatia": "CRO", "Ghana": "GHA",
        "Panama": "PAN", "Uzbekistan": "UZB", "Colombia": "COL",
        "Serbia": "SRB", "Chile": "CHI", "Denmark": "DEN",
        "Poland": "POL", "Ukraine": "UKR", "Romania": "ROU",
        "Nigeria": "NGA", "Cameroon": "CMR", "Mali": "MLI",
        "Venezuela": "VEN", "Peru": "PER", "Honduras": "HON",
        "Costa Rica": "CRC", "Jamaica": "JAM",
    }
    a1 = abbr.get(team1, team1[:3].upper())
    a2 = abbr.get(team2, team2[:3].upper())
    return f"{a1} VS {a2}"
# ─────────────────────────────────────────────────────────────────────────────


# ── SCRAPER ──────────────────────────────────────────────────────────────────
def scrape() -> list[dict]:
    matches = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()

        print("[*] Loading page …")
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)

        print("[*] Waiting for match rows …")
        try:
            page.wait_for_selector(
                "[class*='match-row_matchRowContainer']",
                timeout=30_000
            )
        except PWTimeout:
            print("[!] Selector not found – waiting 10s …")
            time.sleep(10)

        time.sleep(3)

        print("[*] Scrolling to load all matches …")
        prev_height = 0
        for i in range(MAX_SCROLLS):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(SCROLL_PAUSE)
            new_height = page.evaluate("document.body.scrollHeight")
            rows_so_far = len(page.query_selector_all("[class*='match-row_matchRowContainer']"))
            print(f"    scroll {i+1:02d} | height {new_height:,} | rows: {rows_so_far}")
            if new_height == prev_height:
                print("    → no more content")
                break
            prev_height = new_height

        print("[*] Parsing match data …")

        # We only need match rows here (not date headers) since we're not
        # touching Date/Group — but we still walk the same element list so
        # 'Match Number' (row order) stays IDENTICAL to what SCHEDULE used
        # originally. This is critical: Match Number must line up with the
        # existing Supabase rows, or updates will land on the wrong match.
        elements = page.query_selector_all(
            "[class*='matches-container_header'], "
            "[class*='match-row_matchRowContainer']"
        )
        print(f"[*] Total elements found: {len(elements)}")

        for el in elements:
            cls = el.get_attribute("class") or ""

            # Skip date headers — they don't count toward Match Number,
            # same as the original scraper's numbering logic.
            if "matches-container_header" in cls:
                continue

            # ── Match row ───────────────────────────────────────────────
            teams = el.query_selector_all("span.d-none.d-md-block")
            team_names = [clean(t.inner_text()) for t in teams if clean(t.inner_text())]

            if len(team_names) < 2:
                team_divs = el.query_selector_all("[class*='match-row_team']")
                for div in team_divs:
                    spans = div.query_selector_all("span")
                    for s in spans:
                        txt = clean(s.inner_text())
                        if txt and txt not in team_names:
                            team_names.append(txt)
                            break

            if len(team_names) < 2:
                continue

            team1, team2 = team_names[0], team_names[1]

            # ── Extract result if match is played ──────────────────────
            status_div = el.query_selector("[class*='match-row_matchRowStatus']")
            results = None  # None = omit field, don't touch Supabase value

            if status_div:
                score_spans  = status_div.query_selector_all("[class*='match-row_score']")
                score_values = [clean(s.inner_text()) for s in score_spans if clean(s.inner_text()).isdigit()]
                if len(score_values) >= 2:
                    results = f"{score_values[0]} - {score_values[1]}"

            match_number = len(matches) + 1

            row = {
                "Match Number": match_number,
                "Fixture":      f"{team1} vs {team2}",
                "Short Name":   make_short_name(team1, team2),
            }
            if results is not None:
                row["Results"] = results

            matches.append(row)

        browser.close()

    print(f"[✓] Scraped {len(matches)} matches")
    return matches
# ─────────────────────────────────────────────────────────────────────────────


# ── SUPABASE UPDATE ────────────────────────────────────────────────────────────
def push_to_supabase(matches: list[dict]) -> None:
    """
    Updates ONLY Fixture, Short Name, and (when available) Results for
    each row, matched by Match Number. Uses .update().eq() — NOT upsert —
    so Date, Group, and Kick-off Time are never touched, and no new rows
    are ever created.
    """
    if not matches:
        print("[!] No data to push.")
        return

    print("[*] Connecting to Supabase …")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    updated = 0
    failed  = 0

    for m in matches:
        match_number = m["Match Number"]
        payload = {k: v for k, v in m.items() if k != "Match Number"}

        try:
            resp = (
                supabase
                .table(TABLE_NAME)
                .update(payload)
                .eq("Match Number", match_number)
                .execute()
            )

            if resp.data:
                updated += 1
            else:
                failed += 1
                print(f"    [!] No row found for Match Number {match_number} — check it exists in Supabase")

        except Exception as e:
            failed += 1
            print(f"    [✗] Error updating Match Number {match_number}: {e}")

    print(f"\n[✓] Done! {updated} updated | {failed} failed")
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    data = scrape()
    if data:
        push_to_supabase(data)
    else:
        print("[!] No data scraped.")
        print("    → Try setting headless=False to debug.")