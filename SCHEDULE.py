"""
FIFA World Cup 2026 – Results-Only Updater → Supabase
=====================================================
This script ONLY updates the 'Results' column for completed matches.
It NEVER touches Date, Group, Fixture, Short Name, or Kick-off Time.

Match identity: matched by 'Fixture' column (e.g. "Mexico vs South Africa")

Source  : https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures
Output  : Supabase table → FIFA World Cup Schedule - Live (Results column only)

Run
---
    python fifa_wc2026_results_updater.py
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


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def scrape_results_only() -> list[dict]:
    """
    Returns a list of dicts for COMPLETED matches only:
        { "Fixture": "Mexico vs South Africa", "Results": "2 - 0" }

    Logic per match row:
    - If match-row_matchRowStatus exists  → match played → extract scores
    - If match-row_matchTime exists       → upcoming     → SKIP entirely
    """
    results = []

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

        print("[*] Loading FIFA fixtures page …")
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
            rows_so_far = len(
                page.query_selector_all("[class*='match-row_matchRowContainer']")
            )
            print(f"    scroll {i+1:02d} | height {new_height:,} | rows: {rows_so_far}")
            if new_height == prev_height:
                print("    → no more content")
                break
            prev_height = new_height

        print("[*] Parsing match rows for results …")

        rows = page.query_selector_all("[class*='match-row_matchRowContainer']")
        print(f"[*] Total match rows found: {len(rows)}")

        completed = 0
        skipped   = 0

        for row in rows:
            # ── Check if match is played (status div exists) ──────────────
            status_div = row.query_selector("[class*='match-row_matchRowStatus']")
            time_span  = row.query_selector("[class*='match-row_matchTime']")

            if time_span and not status_div:
                # Upcoming match — time is shown, no score yet → SKIP
                skipped += 1
                continue

            if not status_div:
                # Neither status nor time — unusual, skip
                skipped += 1
                continue

            # ── Extract team names (same logic as original scraper) ───────
            teams = row.query_selector_all("span.d-none.d-md-block")
            team_names = [clean(t.inner_text()) for t in teams if clean(t.inner_text())]

            if len(team_names) < 2:
                team_divs = row.query_selector_all("[class*='match-row_team']")
                for div in team_divs:
                    spans = div.query_selector_all("span")
                    for s in spans:
                        txt = clean(s.inner_text())
                        if txt and txt not in team_names:
                            team_names.append(txt)
                            break

            if len(team_names) < 2:
                print(f"    [!] Could not extract team names for a row — skipping")
                skipped += 1
                continue

            team1, team2 = team_names[0], team_names[1]
            fixture = f"{team1} vs {team2}"

            # ── Extract scores from the two score spans ───────────────────
            # HTML structure inside match-row_matchRowStatus:
            #   <span class="match-row_score__wfcQP match-row_scoreWinner__KB4p-">2</span>
            #   <div class="match-row_status__kFtCL">
            #       <span class="match-row_statusLabel__AiSA3 match-row_fullTime__muXhs">FT</span>
            #   </div>
            #   <span class="match-row_score__wfcQP match-row_scoreLoser__wNbgU">0</span>

            score_spans = status_div.query_selector_all("[class*='match-row_score']")
            score_values = [clean(s.inner_text()) for s in score_spans if clean(s.inner_text()).isdigit()]

            if len(score_values) < 2:
                # Score not yet available (e.g. match in progress with no score shown)
                print(f"    [~] {fixture} — status div found but scores not ready, skipping")
                skipped += 1
                continue

            score1, score2 = score_values[0], score_values[1]
            result_str = f"{score1} - {score2}"

            results.append({
                "Fixture": fixture,
                "Results": result_str,
            })
            completed += 1
            print(f"    [✓] {fixture}: {result_str}")

        browser.close()

    print(f"\n[✓] Scraped {completed} completed results | {skipped} upcoming/skipped")
    return results


def push_results_to_supabase(results: list[dict]) -> None:
    """
    For each completed match, update ONLY the Results column.
    Matches by Fixture string. Uses .update() with .eq() — NOT upsert —
    so no other column is ever touched.
    """
    if not results:
        print("[!] No completed results to push.")
        return

    print("[*] Connecting to Supabase …")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    updated = 0
    failed  = 0

    for match in results:
        fixture = match["Fixture"]
        result  = match["Results"]

        try:
            resp = (
                supabase
                .table(TABLE_NAME)
                .update({"Results": result})          # ONLY Results column
                .eq("Fixture", fixture)               # match by Fixture string
                .execute()
            )

            if resp.data:
                updated += 1
                print(f"    [✓] Updated '{fixture}' → {result}")
            else:
                # Could mean fixture string doesn't match exactly in DB
                print(f"    [!] No row found for fixture: '{fixture}' — check spelling")
                failed += 1

        except Exception as e:
            print(f"    [✗] Error updating '{fixture}': {e}")
            failed += 1

    print(f"\n[✓] Done! {updated} updated | {failed} failed")


if __name__ == "__main__":
    data = scrape_results_only()
    if data:
        push_results_to_supabase(data)
    else:
        print("[!] No completed matches found.")
