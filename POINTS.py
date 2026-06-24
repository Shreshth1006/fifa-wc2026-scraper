"""
FIFA World Cup 2026 – Standings Scraper → Supabase
====================================================
Source  : https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/standings
Output  : Supabase table  →  FIFA World Cup Points Table - Live

Columns : Group | Rank | Country Name | Short Name | P | W | D | L | GD | Points

FIXES APPLIED (verified via terminal diagnostic — all 48 rows correct):
  1. Points was read from stats[7], but the actual cell order is
     P, W, D, L, GF, GA, GD, TCS, Pts (9 cells) — TCS is a hidden
     tiebreaker stat sitting between GD and Pts. Fixed to stats[8].
  2. Group caption text is "Standings and Group Tables - Group A".
     The phrase "Group Tables" was matching before the real "Group A",
     causing every row to read Group="T". Fixed by anchoring the
     regex to the end of the string with \\s*$.

Requirements
------------
    pip install playwright supabase python-dotenv
    playwright install chromium

Run
---
    python POINTS.py
"""

import time
import re
import os
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from supabase import create_client, Client
from dotenv import load_dotenv

# ── CONFIG ────────────────────────────────────────────────────────────────────
load_dotenv()

URL          = "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/standings"
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
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

            # FIX #2: caption text is "Standings and Group Tables - Group A".
            # "Group Tables" appears before the real "Group A", so the regex
            # must be anchored to the end of the string to avoid matching
            # "Group T" (from "Tables") instead of the actual group letter.
            inner_div = caption_el.query_selector("[class*='standings-table-head_text']")
            if inner_div:
                title_attr = inner_div.get_attribute("title")
                inner_text = inner_div.inner_text()
            else:
                title_attr = caption_el.get_attribute("title")
                inner_text = caption_el.inner_text()

            caption_text = (title_attr or inner_text or "").strip()

            group_match = re.search(r"Group\s+([A-Z])\s*$", caption_text)
            if not group_match:
                print(f"    [!] Could not parse group from caption: '{caption_text}' — skipping table")
                continue
            group = group_match.group(1)   # "A", "B", "C" … to match your sheet

            team_rows = container.query_selector_all("tbody tr")
            for rank_idx, tr in enumerate(team_rows, 1):

                # Short name / abbreviation  e.g. NLD, ENG
                abbr_el = tr.query_selector("[class*='team-abbreviations_container'] span")
                short_name = abbr_el.inner_text().strip() if abbr_el else ""

                # Full country name
                full_name_el = tr.query_selector("span.d-none")
                full_name = full_name_el.inner_text().strip() if full_name_el else short_name

                # Stat cells — CONFIRMED order: P W D L GF GA GD TCS Pts (9 cells)
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
                    "Points":       safe_int(stats, 8),  # FIX #1: skip TCS(7) too
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