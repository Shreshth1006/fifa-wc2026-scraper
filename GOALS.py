"""
FIFA World Cup 2026 – Goalkeeping Stats Scraper → Supabase
=============================================================
Scrapes the "Goalkeeping" tab on FIFA.com's Player Statistics page.

Columns captured (exactly 4, as requested):
    Rank | Name | Country | Goalkeeper Saves

Source  : https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/statistics/player-statistics
Output  : Supabase table → "FIFA World Cup Goalkeeping - Live"

DOM structure (confirmed via inspect-element):
    <td class="sticky-column rank-column">1</td>
    <td class="sticky-column list-cell-column">
        <div class="list-cell">
            <div class="avatar avatar--player sm">...</div>
            <div class="content-container">
                <div class="main-text">Eloy Room</div>
                <div class="extra-info-container">
                    <p class="extra-info-description ...">
                        <span class="dsk-description">CUW</span>
                        <span class="mob-description">CUW</span>
                    </p>
                    <p class="extra-info-description">GK</p>   <!-- position -->
                </div>
            </div>
        </div>
    </td>
    <td class="scrollable-column">21</td>   <!-- Goalkeeper Saves -->
    <td class="scrollable-column">81</td>   <!-- Actions Inside (skipped) -->
    <td class="scrollable-column">72</td>   <!-- Actions Outside (skipped) -->

Requirements
------------
    pip install playwright supabase python-dotenv
    playwright install chromium

Run
---
    python fifa_goalkeeping_scraper.py
"""

import time
import re
import os
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from supabase import create_client, Client
from dotenv import load_dotenv

# ── CONFIG ────────────────────────────────────────────────────────────────────
load_dotenv()

URL = "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/statistics/player-statistics"

SUPABASE_URL = os.environ.get("SUPABASE_URL_WIDGET")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY_WIDGET")
TABLE_NAME   = "FIFA World Cup Goalkeeping - Live"
# ─────────────────────────────────────────────────────────────────────────────


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


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

        print("[*] Loading player statistics page …")
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(3)

        # ── Dismiss OneTrust cookie consent overlay if present ─────────────
        # This overlay blocks all click events on the page underneath it
        # until dismissed — root cause of the tab-click failures.
        print("[*] Checking for cookie consent banner …")
        try:
            accept_btn = page.locator("#onetrust-accept-btn-handler")
            accept_btn.wait_for(state="visible", timeout=8_000)
            accept_btn.click()
            print("    → Cookie banner dismissed")
            time.sleep(1.5)
        except PWTimeout:
            print("    → No cookie banner found (or already dismissed)")

        # Extra safety: forcibly remove the overlay via JS in case the
        # button click didn't fully clear it (e.g. fade-out animation
        # still blocking pointer events for a moment)
        page.evaluate("""
            () => {
                const overlay = document.querySelector('.onetrust-pc-dark-filter');
                if (overlay) overlay.remove();
                const sdk = document.querySelector('#onetrust-consent-sdk');
                if (sdk) sdk.style.display = 'none';
            }
        """)
        time.sleep(1)

        # ── Click the "Goalkeeping" filter chip ────────────────────────────
        print("[*] Clicking 'Goalkeeping' tab …")
        try:
            # Confirmed via inspect-element: button has class "filter-chip"
            # and contains <span class="filter-chip__label">Goalkeeping</span>
            goalkeeping_btn = page.locator(
                "button.filter-chip:has(span.filter-chip__label:text-is('Goalkeeping'))"
            ).first
            goalkeeping_btn.wait_for(state="visible", timeout=15_000)
            goalkeeping_btn.click(timeout=15_000)
            print("    → Clicked successfully")
            time.sleep(3)

            # Verify the click actually worked by checking aria-pressed
            is_selected = goalkeeping_btn.get_attribute("aria-pressed")
            print(f"    [DEBUG] aria-pressed after click: {is_selected}")

        except PWTimeout:
            print("[!] Could not find/click 'Goalkeeping' tab — page structure may have changed")
            # Fallback: try simpler text-based locator
            try:
                print("    → Trying fallback locator …")
                fallback_btn = page.get_by_text("Goalkeeping", exact=True).first
                fallback_btn.click(timeout=10_000)
                print("    → Fallback click succeeded")
                time.sleep(3)
            except Exception as e2:
                print(f"    [✗] Fallback also failed: {e2}")

        print("[*] Waiting for table to load …")
        try:
            page.wait_for_selector("table.table--full-width tbody tr", timeout=20_000)
        except PWTimeout:
            print("[!] Table rows not found — waiting 10s and retrying …")
            time.sleep(10)

        time.sleep(3)

        # ── DEBUG: confirm which tab's table is actually showing ──────────
        header_cells = page.query_selector_all("table.table--full-width thead th")
        header_texts = [clean(h.inner_text()) for h in header_cells]
        print(f"    [DEBUG] Table header columns right now: {header_texts}")

        # ── SAFETY CHECK: abort if we're not actually on the Goalkeeping tab ──
        header_blob = " ".join(header_texts).lower()
        if "goalkeeper" not in header_blob and "save" not in header_blob:
            print("\n[✗] ABORTING: Table headers don't mention 'Goalkeeper' or 'Save'.")
            print(f"    This means the tab click failed and we're reading the WRONG tab.")
            print(f"    Headers seen: {header_texts}")
            print("    Refusing to scrape/push to avoid corrupting Supabase with wrong data.")
            browser.close()
            return []
        # ──────────────────────────────────────────────────────────────────────

        # Scroll to load any lazy-rendered rows
        print("[*] Scrolling to load all rows …")
        prev_count = 0
        for i in range(30):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.5)
            count = len(page.query_selector_all("table.table--full-width tbody tr"))
            print(f"    scroll {i+1:02d} | rows so far: {count}")
            if count == prev_count:
                print("    → no more rows loading")
                break
            prev_count = count

        # ── Parse table rows ────────────────────────────────────────────────
        print("[*] Parsing goalkeeping table …")
        table_rows = page.query_selector_all("table.table--full-width tbody tr")
        print(f"[*] Found {len(table_rows)} player rows")

        for tr in table_rows:
            rank_el = tr.query_selector("td.rank-column")
            rank_text = clean(rank_el.inner_text()) if rank_el else ""

            name_el = tr.query_selector("td.list-cell-column .main-text")
            name = clean(name_el.inner_text()) if name_el else ""

            # Country code — prefer dsk-description (desktop), fall back to mob-description
            country_el = tr.query_selector("td.list-cell-column .dsk-description")
            if not country_el:
                country_el = tr.query_selector("td.list-cell-column .mob-description")
            country = clean(country_el.inner_text()) if country_el else ""

            # First scrollable-column = Goalkeeper Saves
            stat_cells = tr.query_selector_all("td.scrollable-column")
            saves_text = clean(stat_cells[0].inner_text()) if stat_cells else ""

            if not name:
                continue

            try:
                rank = int(rank_text) if rank_text else None
            except ValueError:
                rank = None

            try:
                saves = int(saves_text) if saves_text else 0
            except ValueError:
                saves = 0

            rows.append({
                "Rank":              rank,
                "Name":              name,
                "Country":           country,
                "Goalkeeper Saves":  saves,
            })

        browser.close()

    print(f"[✓] Scraped {len(rows)} goalkeeper rows")
    return rows


# ── SUPABASE UPSERT ───────────────────────────────────────────────────────────
def push_to_supabase(rows: list[dict]) -> None:
    if not rows:
        print("[!] No rows to push.")
        return

    print("[*] Connecting to Supabase …")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"[*] Upserting {len(rows)} rows into '{TABLE_NAME}' …")

    batch_size = 50
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        supabase.table(TABLE_NAME).upsert(
            batch,
            on_conflict="Name,Country"
        ).execute()
        total += len(batch)
        print(f"    → Pushed batch {i // batch_size + 1} ({len(batch)} rows)")

    print(f"[✓] Done! {total} rows upserted to '{TABLE_NAME}'")


if __name__ == "__main__":
    data = scrape()
    if data:
        push_to_supabase(data)
    else:
        print("[!] No data scraped.")
        print("    → Try setting headless=False to debug.")