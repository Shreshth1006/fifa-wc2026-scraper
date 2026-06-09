"""
FIFA World Cup 2026 – Standings Scraper → Supabase
====================================================
Source  : https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/standings
Output  : Supabase table  →  FIFA World Cup Points Table - Live

Columns : Group | Rank | Country Name | Short Name | P | W | D | L | GD | Points

Requirements
------------
    pip install playwright supabase
    playwright install chromium

Run
---
    python fifa_wc2026_supabase_scraper.py
"""

import time
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from supabase import create_client, Client

# ── CONFIG ────────────────────────────────────────────────────────────────────
URL          = "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/standings"
SUPABASE_URL = "https://iysiejpiupmcxynhxsyj.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml5c2llanBpdXBtY3h5bmh4c3lqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk4Njk4NTQsImV4cCI6MjA5NTQ0NTg1NH0.s9_4IhS2HGq_4PyJqFJWcIGYxaahn9mkWIk1CguFmNE"
TABLE_NAME   = "FIFA World Cup Points Table - Live"
# ─────────────────────────────────────────────────────────────────────────────


def scrape() -> list[dict]:
    rows = []

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

        print(f"[*] Loading standings page …")
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)

        print("[*] Waiting for standings tables …")
        try:
            page.wait_for_selector(
                "[class*='standings-table_standingsTableContainer']",
                timeout=30_000
            )
        except PWTimeout:
            print("[!] Selector not found – waiting 10s …")
            time.sleep(10)

        time.sleep(3)

        # Scroll to load all groups (lazy rendering)
        print("[*] Scrolling to load all groups …")
        prev_height = 0
        for i in range(40):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            new_height = page.evaluate("document.body.scrollHeight")
            tables = len(page.query_selector_all("[class*='standings-table_standingsTableContainer']"))
            print(f"    scroll {i+1:02d} | height {new_height:,} | group tables: {tables}")
            if new_height == prev_height:
                print("    → no more content")
                break
            prev_height = new_height

        # ── Parse each group table ────────────────────────────────────────
        print("[*] Parsing standings …")
        containers = page.query_selector_all(
            "[class*='standings-table_standingsTableContainer']"
        )
        print(f"[*] Found {len(containers)} group containers")

        for container in containers:
            caption_el = container.query_selector("[class*='standings-table-head_tableCaption']")
            if not caption_el:
                continue
            caption_text = caption_el.inner_text().strip()
            group_match = re.search(r"Group\s+([A-Z])", caption_text)
            if not group_match:
                continue
            group = group_match.group(1)   # just "A", "B", "C" … to match your sheet

            team_rows = container.query_selector_all("tbody tr")
            for rank_idx, tr in enumerate(team_rows, 1):

                # Short name / abbreviation  e.g. NLD, ENG
                abbr_el = tr.query_selector("[class*='team-abbreviations_container'] span")
                short_name = abbr_el.inner_text().strip() if abbr_el else ""

                # Full country name
                full_name_el = tr.query_selector("span.d-none")
                full_name = full_name_el.inner_text().strip() if full_name_el else short_name

                # Stat cells: P W D L GF GA GD Pts
                stat_cells = tr.query_selector_all("[class*='standings-table-row_stats']")
                stats = [c.inner_text().strip() for c in stat_cells]

                def safe_int(lst, i):
                    try:
                        return int(lst[i])
                    except (IndexError, ValueError):
                        return 0

                rows.append({
                    "Group":        group,
                    "Rank":         rank_idx,
                    "Country Name": full_name,
                    "Short Name":   short_name,
                    "P":            safe_int(stats, 0),
                    "W":            safe_int(stats, 1),
                    "D":            safe_int(stats, 2),
                    "L":            safe_int(stats, 3),
                    "GD":           safe_int(stats, 6),  # skip GF(4) GA(5)
                    "Points":       safe_int(stats, 7),
                })

        browser.close()

    print(f"[✓] Scraped {len(rows)} team rows across all groups")
    return rows


# ── SUPABASE UPSERT ───────────────────────────────────────────────────────────
def push_to_supabase(rows: list[dict]) -> None:
    if not rows:
        print("[!] No rows to push.")
        return

    print(f"[*] Connecting to Supabase …")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"[*] Upserting {len(rows)} rows into '{TABLE_NAME}' …")

    # Upsert in batches of 50
    batch_size = 50
    total_upserted = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        response = (
            supabase.table(TABLE_NAME)
            .upsert(
                batch,
                on_conflict="Group,Country Name"   # unique constraint columns
            )
            .execute()
        )
        total_upserted += len(batch)
        print(f"    → Pushed batch {i // batch_size + 1} ({len(batch)} rows)")

    print(f"[✓] Done! {total_upserted} rows upserted successfully.")


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    data = scrape()
    if data:
        push_to_supabase(data)
    else:
        print("[!] No data scraped.")
        print("    → Try setting headless=False to debug.")