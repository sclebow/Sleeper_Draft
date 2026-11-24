
import re
import time
from datetime import datetime
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

import truststore

truststore.inject_into_ssl()

BASE_URL = "https://www.dynastynerds.com"
SERIES_URL = f"{BASE_URL}/series/mock-draft/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DynastyMockResearch/1.0; "
        "+https://www.dynastynerds.com/)"
    )
}

REQUEST_DELAY = 1.0
TIMEOUT = 20

session = requests.Session()
session.headers.update(HEADERS)


def get_soup(url):
    """Download a page and return its BeautifulSoup document."""
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def discover_mock_articles():
    """
    Follow the series archive pagination and collect 2027 rookie
    mock draft article URLs.
    """
    article_urls = set()
    visited_pages = set()
    next_page = SERIES_URL

    while next_page and next_page not in visited_pages:
        print(f"Reading archive: {next_page}")
        visited_pages.add(next_page)

        soup = get_soup(next_page)

        for link in soup.select("a[href]"):
            href = urljoin(BASE_URL, link["href"])
            title = clean_text(link.get_text(" ", strip=True))

            # Article URLs on Dynasty Nerds use /mock/...
            if "/mock/" not in href:
                continue

            if "2027" in title.lower() and "rookie mock draft" in title.lower():
                article_urls.add(href.split("#")[0])

        # Find the archive's Next pagination link.
        next_link = None
        for link in soup.select("a[href]"):
            label = clean_text(link.get_text(" ", strip=True)).lower()
            if label in {"next", "next →", "next »"}:
                next_link = urljoin(BASE_URL, link["href"])
                break

        next_page = next_link

        if next_page:
            time.sleep(REQUEST_DELAY)

    return sorted(article_urls)


def parse_pick_heading(text):
    """
    Parse headings such as:
    1.01 | Jeremiah Smith | WR | Ohio State | 6'4" | 222 lbs
    """
    text = clean_text(text)

    match = re.match(
        r"^\s*(\d{1,2})\.(\d{1,2})\s*\|\s*"
        r"([^|]+?)\s*\|\s*"
        r"(QB|RB|WR|TE|K|DL|LB|DB|IDP)\s*\|\s*"
        r"([^|]+)",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    round_number = int(match.group(1))
    pick_in_round = int(match.group(2))

    return {
        "pick": f"{round_number}.{pick_in_round:02d}",
        "round": round_number,
        "pick_in_round": pick_in_round,
        "player": clean_text(match.group(3)),
        "position": match.group(4).upper(),
        "school": clean_text(match.group(5)),
    }


def parse_pick_tables(content):
    """Extract picks from HTML tables, when an article uses them."""
    picks = []

    for table in content.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        headers = [
            clean_text(cell.get_text(" ", strip=True)).lower()
            for cell in rows[0].find_all(["th", "td"])
        ]

        # Locate columns by their header names.
        pick_col = next(
            (i for i, h in enumerate(headers) if h in {"pick", "selection"}),
            None,
        )
        player_col = next(
            (i for i, h in enumerate(headers) if "player" in h),
            None,
        )
        pos_col = next(
            (i for i, h in enumerate(headers) if h in {"pos", "position", "pos."}),
            None,
        )
        school_col = next(
            (i for i, h in enumerate(headers) if "school" in h or "college" in h),
            None,
        )
        drafter_col = next(
            (i for i, h in enumerate(headers) if "drafter" in h or "manager" in h),
            None,
        )

        if pick_col is None or player_col is None:
            continue

        for row in rows[1:]:
            cells = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["th", "td"])
            ]

            if len(cells) <= max(pick_col, player_col):
                continue

            pick_match = re.match(r"(\d{1,2})\.(\d{1,2})", cells[pick_col])
            if not pick_match:
                continue

            round_number = int(pick_match.group(1))
            pick_in_round = int(pick_match.group(2))

            picks.append({
                "pick": f"{round_number}.{pick_in_round:02d}",
                "round": round_number,
                "pick_in_round": pick_in_round,
                "player": cells[player_col],
                "position": (
                    cells[pos_col].upper()
                    if pos_col is not None and len(cells) > pos_col
                    else None
                ),
                "school": (
                    cells[school_col]
                    if school_col is not None and len(cells) > school_col
                    else None
                ),
                "drafter": (
                    cells[drafter_col]
                    if drafter_col is not None and len(cells) > drafter_col
                    else None
                ),
            })

    return picks


def parse_pick_headings(content):
    """Extract picks from headings such as '1.01 | Player | WR | School'."""
    picks = []

    for heading in content.find_all(re.compile(r"^h[1-6]$")):
        pick = parse_pick_heading(heading.get_text(" ", strip=True))

        if not pick:
            continue

        # In this article layout, the drafter is often the paragraph
        # immediately following the pick heading.
        drafter = None
        sibling = heading.find_next_sibling()

        if sibling and sibling.name in {"p", "div"}:
            possible_name = clean_text(sibling.get_text(" ", strip=True))

            # Avoid treating a full paragraph of analysis as a drafter.
            if len(possible_name.split()) <= 4 and len(possible_name) < 50:
                drafter = possible_name

        pick["drafter"] = drafter
        picks.append(pick)

    return picks


def parse_mock_article(url):
    """Extract metadata and picks from one mock draft article."""
    soup = get_soup(url)

    title_tag = soup.find("h1")
    title = clean_text(title_tag.get_text(" ", strip=True)) if title_tag else ""

    # Prefer the article's content area over the full page.
    content = (
        soup.select_one(".entry-content")
        or soup.select_one("article")
        or soup.find("main")
        or soup
    )

    # Published date.
    date_tag = soup.find("time")
    published = None

    if date_tag:
        published = date_tag.get("datetime") or clean_text(
            date_tag.get_text(" ", strip=True)
        )

    # Description / introductory text.
    paragraphs = [
        clean_text(p.get_text(" ", strip=True))
        for p in content.find_all("p")
    ]
    intro = next((p for p in paragraphs if p), "")

    # Try tables first, then headings.
    picks = parse_pick_tables(content)

    if not picks:
        picks = parse_pick_headings(content)

    # Avoid duplicate picks if an article has multiple representations.
    unique_picks = {}
    for pick in picks:
        unique_picks[pick["pick"]] = pick

    picks = list(unique_picks.values())
    picks.sort(key=lambda x: (x["round"], x["pick_in_round"]))

    # Infer some useful metadata from the title and introduction.
    combined_text = f"{title} {intro}".lower()

    if "superflex" in combined_text or re.search(r"\bsf\b", title.lower()):
        league_format = "Superflex"
    elif "1qb" in combined_text:
        league_format = "1QB"
    else:
        league_format = None

    tep = "TEP" if (
        "tight end premium" in combined_text
        or re.search(r"\btep\b", combined_text)
    ) else None

    mock = {
        "mock_id": url.rstrip("/").split("/")[-1],
        "source": "Dynasty Nerds",
        "title": title,
        "url": url,
        "published": published,
        "format": league_format,
        "premium": tep,
        "rounds": max((p["round"] for p in picks), default=None),
        "pick_count": len(picks),
    }

    for pick in picks:
        pick["mock_id"] = mock["mock_id"]
        pick["source"] = "Dynasty Nerds"
        pick["url"] = url

    return mock, picks


def scrape_dynasty_nerds(verbose=False):
    """Main scraper: discover articles and build two DataFrames."""
    article_urls = discover_mock_articles()

    if verbose:
        print(f"\nFound {len(article_urls)} candidate articles.")

    mock_rows = []
    pick_rows = []

    for index, url in enumerate(article_urls, start=1):
        if verbose:
            print(f"[{index}/{len(article_urls)}] Scraping {url}")

        try:
            mock, picks = parse_mock_article(url)

            if verbose:
                if not picks:
                    print("  WARNING: No picks extracted.")
                else:
                    print(f"  Extracted {len(picks)} picks.")

            mock_rows.append(mock)
            pick_rows.extend(picks)

        except requests.RequestException as exc:
            if verbose:
                print(f"  Request error: {exc}")
        except Exception as exc:
            if verbose:
                print(f"  Parsing error: {exc}")

        time.sleep(REQUEST_DELAY)

    mock_drafts = pd.DataFrame(mock_rows)
    mock_picks = pd.DataFrame(pick_rows)

    if not mock_picks.empty:
        mock_picks = mock_picks.sort_values(
            ["mock_id", "round", "pick_in_round"]
        ).reset_index(drop=True)

    return mock_drafts, mock_picks


if __name__ == "__main__":
    mock_drafts, mock_picks = scrape_dynasty_nerds()

    print("\nMOCK DRAFTS")
    print(mock_drafts.to_string(index=False))

    print("\nMOCK PICKS")
    print(mock_picks.head(20).to_string(index=False))

    mock_drafts.to_csv("dynasty_nerds_mocks.csv", index=False)
    mock_picks.to_csv("dynasty_nerds_picks.csv", index=False)

    print("\nSaved CSV files.")