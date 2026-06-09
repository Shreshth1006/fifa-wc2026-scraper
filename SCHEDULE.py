"""
FIFA World Cup 2026 – Schedule Scraper → Supabase
==================================================
Source  : https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures
Output  : Supabase table → FIFA World Cup Schedule - Live

Columns : Date | Group | Fixture | Short Name | Kick-off Time (IST) | Results

Requirements
------------
    pip install playwright supabase
    playwright install chromium

Run
---
    python fifa_wc2026_schedule_supabase.py
"""

import time
import re
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from supabase import create_client, Client

# ── CONFIG ────────────────────────────────────────────────────────────────────
URL          = (
    "https://www.fifa.com/en/tournaments/mens/worldcup/"
    "canadamexicousa2026/scores-fixtures?country=&wtw-filter=ALL"
)
SUPABASE_URL  = "https://iysiejpiupmcxynhxsyj.supabase.co"
SUPABASE_KEY  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml5c2llanBpdXBtY3h5bmh4c3lqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk4Njk4NTQsImV4cCI6MjA5NTQ0NTg1NH0.s9_4IhS2HGq_4PyJqFJWcIGYxaahn9mkWIk1CguFmNE"
TABLE_NAME    = "FIFA World Cup Schedule - Live"
SCROLL_PAUSE  = 2.5
MAX_SCROLLS   = 80
# ─────────────────────────────────────────────────────────────────────────────


# ── HELPERS ──────────────────────────────────────────────────────────────────
def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_date(raw: str) -> str:
    """'Friday 12 June 2026' → 'Jun 12'"""
    for fmt in ("%A %d %B %Y", "%A, %d %B %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%b %d")
        except ValueError:
            continue
    return raw.strip()


def extract_group(label_text: str) -> str:
    parts = [p.strip() for p in label_text.split("·")]
    for part in parts:
        m = re.search(r"Group\s+([A-Z])", part)
        if m:
            return f"Group {m.group(1)}"
    return parts[0] if parts else ""


def extract_venue(label_text: str) -> str:
    parts = [p.strip() for p in label_text.split("·")]
    return parts[-1] if parts else ""


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


def utc_to_ist(time_str: str) -> str:
    try:
        h, m = map(int, time_str.strip().split(":"))
        ampm = "AM" if h < 12 else "PM"
        h12  = h % 12 or 12
        return f"{h12}:{m:02d} {ampm}"
    except Exception:
        return time_str
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
        current_date = ""

        elements = page.query_selector_all(
            "[class*='matches-container_header'], "
            "[class*='match-row_matchRowContainer']"
        )
        print(f"[*] Total elements found: {len(elements)}")

        for el in elements:
            cls = el.get_attribute("class") or ""

            # Date header
            if "matches-container_header" in cls:
                title_el = el.query_selector("[class*='matches-container_title']")
                if title_el:
                    current_date = parse_date(clean(title_el.inner_text()))
                continue

            # Match row
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

            time_el     = el.query_selector("[class*='match-row_matchTime']")
            kickoff_raw = clean(time_el.inner_text()) if time_el else ""
            kickoff     = utc_to_ist(kickoff_raw)

            label_el  = el.query_selector("[class*='match-row_bottomLabelWrapper']")
            label_txt = clean(label_el.inner_text()) if label_el else ""

            # Results — score if available, empty if not played yet
            score_el = el.query_selector("[class*='match-row_score']")
            results  = clean(score_el.inner_text()) if score_el else ""

            matches.append({
                "Match Number":        len(matches) + 1,
                "Date":               current_date,
                "Group":              extract_group(label_txt),
                "Fixture":            f"{team1} vs {team2}",
                "Short Name":         make_short_name(team1, team2),
                "Kick-off Time (IST)": kickoff,
                "Results":            results,
            })

        browser.close()

    print(f"[✓] Scraped {len(matches)} matches")
    return matches
# ─────────────────────────────────────────────────────────────────────────────


# ── SUPABASE UPSERT x───────────────────────────────────────────────────────────
def push_to_supabase(matches: list[dict]) -> None:
    if not matches:
        print("[!] No data to push.")
        return

    print("[*] Connecting to Supabase …")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print(f"[*] Upserting {len(matches)} rows into '{TABLE_NAME}' …")

    batch_size = 50
    total = 0

    for i in range(0, len(matches), batch_size):
        batch = matches[i : i + batch_size]
        supabase.table(TABLE_NAME).upsert(
            batch,
           on_conflict="Match Number"   # unique: same fixture on same date
        ).execute()
        total += len(batch)
        print(f"    → Pushed batch {i // batch_size + 1} ({len(batch)} rows)")

    print(f"[✓] Done! {total} rows upserted to '{TABLE_NAME}'")
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    data = scrape()
    if data:
        push_to_supabase(data)
    else:
        print("[!] No data scraped.")
        print("    → Try setting headless=False to debug.")