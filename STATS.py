"""
FIFA World Cup 2026 – ESPN Stats Scraper → Supabase (Playwright version)
============================================================================
CORRECTION: ESPN's stats pages are JavaScript-rendered (React), NOT plain
server-rendered HTML. A plain requests.get() returns zero <table> elements
because nothing is in the raw response — the tables get injected by JS
after page load. This version uses Playwright to render the page properly
before parsing, same approach as our FIFA.com scrapers.

Scrapes THREE tables, each pushed to its own Supabase table, preserving
on-page row order:

  1. Top Scorers   → "FIFA World Cup Top Scorers - Live"
  2. Top Assists    → "FIFA World Cup Top Assists - Live"
  3. Discipline     → "FIFA World Cup Discipline - Live"  (team-level)

Requirements
------------
    pip install playwright supabase python-dotenv
    playwright install chromium

Run
---
    python STATS.py
"""

import os
import re
import time
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from supabase import create_client, Client
from dotenv import load_dotenv

# ── CONFIG ────────────────────────────────────────────────────────────────────
load_dotenv()

SCORING_URL    = "https://www.espn.in/football/stats/_/league/fifa.world"
DISCIPLINE_URL = "https://www.espn.in/football/stats/_/league/FIFA.WORLD/view/discipline"

SUPABASE_URL = os.environ.get("SUPABASE_URL_WIDGET")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY_WIDGET")

TABLE_GOALS      = "FIFA World Cup Top Scorers - Live"
TABLE_ASSISTS     = "FIFA World Cup Top Assists - Live"
TABLE_DISCIPLINE  = "FIFA World Cup Discipline - Live"
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def load_page(page, url: str):
    print(f"[*] Loading {url} …")
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    time.sleep(3)

    # ── DEBUG: dump what we actually got ───────────────────────────────────
    table_count = page.evaluate("document.querySelectorAll('table').length")
    print(f"    [DEBUG] <table> elements found after JS render: {table_count}")

    all_text_snippets = page.evaluate("""
        () => Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span'))
            .map(el => el.textContent.trim())
            .filter(t => /top scorers|top assists|discipline/i.test(t) && t.length < 60)
    """)
    print(f"    [DEBUG] Text snippets matching scorer/assist/discipline: {all_text_snippets[:10]}")
    # ──────────────────────────────────────────────────────────────────────


def parse_player_table(page, heading_text: str, stat_col_name: str) -> list[dict]:
    """
    Locates a heading containing heading_text, then the nearest following
    <table>, and parses rows as [RK, Name, Team, P, Stat].
    """
    rows = []

    heading_loc = page.locator(f"text=/{re.escape(heading_text)}/i").first
    try:
        heading_loc.wait_for(timeout=10_000)
    except PWTimeout:
        print(f"    [!] Could not find heading '{heading_text}' on page")
        return rows

    # Find the nearest table that comes after this heading in DOM order
    table_handle = page.evaluate_handle(f"""
        () => {{
            const all = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span'));
            const heading = all.find(el => /{re.escape(heading_text)}/i.test(el.textContent) && el.textContent.length < 60);
            if (!heading) return null;
            let el = heading;
            while (el) {{
                const t = el.querySelector ? el.querySelector('table') : null;
                if (t) return t;
                el = el.nextElementSibling || (el.parentElement ? el.parentElement.nextElementSibling : null);
            }}
            return null;
        }}
    """)

    table_el = table_handle.as_element()
    if not table_el:
        print(f"    [!] No table found near heading '{heading_text}'")
        return rows

    tr_elements = table_el.query_selector_all("tr")
    last_rank = None

    for tr in tr_elements:
        cells = tr.query_selector_all("td, th")
        cell_texts = [clean(c.inner_text()) for c in cells]

        if cell_texts and cell_texts[0].upper() == "RK":
            continue
        if not cell_texts or len(cell_texts) < 4:
            continue

        rk_text = cell_texts[0]
        if rk_text:
            try:
                last_rank = int(rk_text)
            except ValueError:
                pass

        name   = cell_texts[1] if len(cell_texts) > 1 else ""
        team   = cell_texts[2] if len(cell_texts) > 2 else ""
        played = cell_texts[3] if len(cell_texts) > 3 else ""
        stat   = cell_texts[4] if len(cell_texts) > 4 else ""

        if not name:
            continue

        rows.append({
            "Rank":         last_rank,
            "Name":         name,
            "Team":         team,
            "P":            int(played) if played.isdigit() else 0,
            stat_col_name:  int(stat) if stat.isdigit() else 0,
        })

    return rows


def parse_discipline_table(page) -> list[dict]:
    rows = []

    table_handle = page.evaluate_handle("""
        () => {
            const all = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span'));
            const heading = all.find(el => /discipline/i.test(el.textContent) && el.textContent.length < 60);
            if (!heading) return null;
            let el = heading;
            while (el) {
                const t = el.querySelector ? el.querySelector('table') : null;
                if (t) return t;
                el = el.nextElementSibling || (el.parentElement ? el.parentElement.nextElementSibling : null);
            }
            return null;
        }
    """)

    table_el = table_handle.as_element()
    if not table_el:
        print("    [!] No discipline table found")
        return rows

    tr_elements = table_el.query_selector_all("tr")
    last_rank = None

    for tr in tr_elements:
        cells = tr.query_selector_all("td, th")
        cell_texts = [clean(c.inner_text()) for c in cells]

        if cell_texts and cell_texts[0].upper() == "RK":
            continue
        if not cell_texts or len(cell_texts) < 5:
            continue

        rk_text = cell_texts[0]
        if rk_text:
            try:
                last_rank = int(rk_text)
            except ValueError:
                pass

        team   = cell_texts[1] if len(cell_texts) > 1 else ""
        played = cell_texts[2] if len(cell_texts) > 2 else ""
        yc     = cell_texts[3] if len(cell_texts) > 3 else ""
        rc     = cell_texts[4] if len(cell_texts) > 4 else ""
        pts    = cell_texts[5] if len(cell_texts) > 5 else ""

        if not team:
            continue

        rows.append({
            "Rank":   last_rank,
            "Team":   team,
            "P":      int(played) if played.isdigit() else 0,
            "YC":     int(yc) if yc.isdigit() else 0,
            "RC":     int(rc) if rc.isdigit() else 0,
            "Points": int(pts) if pts.isdigit() else 0,
        })

    return rows


def scrape_all() -> tuple[list[dict], list[dict], list[dict]]:
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
            viewport={"width": 1280, "height": 1200},
            locale="en-US",
        )
        page = ctx.new_page()

        load_page(page, SCORING_URL)

        print("[*] Parsing Top Scorers …")
        goals = parse_player_table(page, "Top Scorers", "Goals")
        print(f"    → {len(goals)} rows")

        print("[*] Parsing Top Assists …")
        assists = parse_player_table(page, "Top Assists", "Assists")
        print(f"    → {len(assists)} rows")

        load_page(page, DISCIPLINE_URL)
        print("[*] Parsing Discipline …")
        discipline = parse_discipline_table(page)
        print(f"    → {len(discipline)} rows")

        browser.close()

    return goals, assists, discipline


# ── SUPABASE PUSH ──────────────────────────────────────────────────────────────
def push_table(rows: list[dict], table_name: str, conflict_keys: str) -> None:
    if not rows:
        print(f"[!] No rows to push for '{table_name}'.")
        return

    print(f"[*] Connecting to Supabase for '{table_name}' …")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    batch_size = 50
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        supabase.table(table_name).upsert(batch, on_conflict=conflict_keys).execute()
        total += len(batch)

    print(f"[✓] Pushed {total} rows to '{table_name}'")


if __name__ == "__main__":
    goals, assists, discipline = scrape_all()

    push_table(goals,      TABLE_GOALS,      conflict_keys="Name,Team")
    push_table(assists,    TABLE_ASSISTS,    conflict_keys="Name,Team")
    push_table(discipline, TABLE_DISCIPLINE, conflict_keys="Team")

    print("\n[✓] All done!")