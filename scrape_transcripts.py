#!/usr/bin/env python3
"""
Scraper for 10% Happier podcast transcripts from Podscripts.co

Usage:
    pip install requests beautifulsoup4
    python scrape_transcripts.py

Output:
    - transcripts/ directory with one JSON file per episode
    - episodes_index.json with metadata for all episodes
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

# --- Configuration ---
BASE_URL = "https://podscripts.co"
PODCAST_URL = f"{BASE_URL}/podcasts/ten-percent-happier-with-dan-harris/"
OUTPUT_DIR = Path("transcripts")
INDEX_FILE = Path("episodes_index.json")
DELAY_BETWEEN_REQUESTS = 5  # seconds, be polite to the server
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}


def get_soup(url: str) -> BeautifulSoup:
    """Fetch a page and return a BeautifulSoup object."""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def discover_episode_urls() -> list[dict]:
    """
    Crawl the podcast listing pages to collect all episode URLs and basic metadata.
    Podscripts uses infinite scroll / pagination — we'll try incrementing page numbers.
    If that doesn't work, we fall back to sitemap or other discovery methods.
    """
    episodes = []
    page = 1

    while True:
        url = f"{PODCAST_URL}?page={page}" if page > 1 else PODCAST_URL
        print(f"  Fetching episode list page {page}...")

        try:
            soup = get_soup(url)
        except requests.HTTPError as e:
            print(f"  Stopped at page {page}: {e}")
            break

        # Find episode links — adjust selectors based on actual HTML structure
        # Podscripts typically has cards/links with the episode slug
        found_on_page = 0

        # Look for links that match the podcast episode URL pattern
        for link in soup.find_all("a", href=True):
            href = link["href"]
            # Match episode pages (not the main podcast page itself)
            if (
                "/podcasts/ten-percent-happier-with-dan-harris/" in href
                and href.rstrip("/") != "/podcasts/ten-percent-happier-with-dan-harris"
                and href.rstrip("/") != "/podcasts/ten-percent-happier-with-dan-harris/"
            ):
                full_url = urljoin(BASE_URL, href)
                # Extract slug
                slug = href.rstrip("/").split("/")[-1]

                # Try to get title from the link text or nearby heading
                title = link.get_text(strip=True)
                if not title or len(title) < 5:
                    # Look for a heading nearby
                    heading = link.find(["h2", "h3", "h4"])
                    if heading:
                        title = heading.get_text(strip=True)

                # Get date if available (look for nearby date element)
                date_text = ""
                parent = link.find_parent(["div", "article", "li"])
                if parent:
                    date_el = parent.find(string=re.compile(r"Episode Date:"))
                    if date_el:
                        date_text = date_el.strip().replace("Episode Date:", "").strip()
                    else:
                        # Try other date patterns
                        for el in parent.find_all(["span", "p", "time", "small"]):
                            text = el.get_text(strip=True)
                            if re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b.*\d{4}", text):
                                date_text = text
                                break

                # Avoid duplicates
                if not any(e["slug"] == slug for e in episodes):
                    episodes.append({
                        "slug": slug,
                        "url": full_url,
                        "title": title or slug,
                        "date": date_text,
                    })
                    found_on_page += 1

        print(f"    Found {found_on_page} new episodes on page {page} (total: {len(episodes)})")

        if found_on_page == 0:
            print("  No new episodes found, stopping pagination.")
            break

        page += 1
        time.sleep(DELAY_BETWEEN_REQUESTS)

    return episodes


def scrape_transcript(episode: dict) -> dict | None:
    """
    Fetch a single episode page and extract the transcript text.
    Returns the episode dict enriched with transcript content, or None on failure.
    """
    try:
        soup = get_soup(episode["url"])
    except Exception as e:
        print(f"    ERROR fetching {episode['slug']}: {e}")
        return None

    # --- Extract episode metadata from the page ---

    # Title (h1 or og:title)
    h1 = soup.find("h1")
    if h1:
        episode["title"] = h1.get_text(strip=True).replace(
            "Ten Percent Happier with Dan Harris - ", ""
        )

    # Date
    date_match = soup.find(string=re.compile(r"Episode Date:"))
    if date_match:
        date_str = date_match.strip()
        episode["date"] = date_str.replace("Episode Date:", "").strip()

    # Description — often in the expandable section or meta
    desc_el = soup.find("meta", {"name": "description"})
    if desc_el:
        episode["description"] = desc_el.get("content", "")

    # --- Extract transcript ---
    # Podscripts puts the transcript in the page body with "Starting point is HH:MM:SS" markers
    # We'll grab all text content and parse it

    # Strategy 1: Look for a transcript container div
    transcript_text = ""

    # The transcript seems to be in the main content area
    # Look for text blocks that start with "Starting point is"
    page_text = soup.get_text()

    # Extract transcript segments using the timestamp pattern
    segments = []
    # Split on "Starting point is" markers
    parts = re.split(r"Starting point is (\d{2}:\d{2}:\d{2})", page_text)

    # parts will be: [preamble, timestamp1, text1, timestamp2, text2, ...]
    if len(parts) >= 3:
        for i in range(1, len(parts) - 1, 2):
            timestamp = parts[i]
            text = parts[i + 1].strip()
            # Clean up the text
            text = re.sub(r"\s+", " ", text)  # collapse whitespace
            if text:
                segments.append({
                    "timestamp": timestamp,
                    "text": text,
                })

    if not segments:
        print(f"    WARNING: No transcript found for {episode['slug']}")
        return None

    # Combine into full transcript
    full_transcript = "\n\n".join(
        f"[{seg['timestamp']}] {seg['text']}" for seg in segments
    )

    episode["transcript"] = full_transcript
    episode["segments"] = segments
    episode["segment_count"] = len(segments)

    return episode


def clean_transcript_text(text: str) -> str:
    """
    Remove common ad reads and sponsor segments from transcript text.
    This is a best-effort heuristic — you can refine the patterns later.
    """
    # Patterns that typically indicate ad/sponsor segments
    ad_patterns = [
        r"\[.*?\] (?:This episode is sponsored|Sponsored|This podcast is brought to you).*?(?=\[\d{2}:\d{2}:\d{2}\]|\Z)",
        # You can add more patterns here as you review the data
    ]

    cleaned = text
    for pattern in ad_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL)

    return cleaned.strip()


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Step 1: Discover all episode URLs
    print("=" * 60)
    print("STEP 1: Discovering episode URLs")
    print("=" * 60)
    episodes = discover_episode_urls()
    print(f"\nFound {len(episodes)} episodes total.\n")

    if not episodes:
        print("No episodes found. The site structure may have changed.")
        print("Try visiting the site manually and updating the selectors.")
        return

    # Save the index
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(episodes, f, indent=2, ensure_ascii=False)
    print(f"Saved episode index to {INDEX_FILE}")

    # Step 2: Scrape each episode's transcript
    print("\n" + "=" * 60)
    print("STEP 2: Scraping transcripts")
    print("=" * 60)

    success_count = 0
    skip_count = 0

    for i, episode in enumerate(episodes):
        slug = episode["slug"]
        output_file = OUTPUT_DIR / f"{slug}.json"

        # Skip if already scraped
        if output_file.exists():
            skip_count += 1
            continue

        print(f"\n[{i+1}/{len(episodes)}] {episode['title'][:70]}...")
        result = scrape_transcript(episode)

        if result:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            success_count += 1
            print(f"    ✓ Saved ({result['segment_count']} segments)")
        else:
            print(f"    ✗ Failed")

        time.sleep(DELAY_BETWEEN_REQUESTS)

    print("\n" + "=" * 60)
    print(f"DONE: {success_count} scraped, {skip_count} skipped (already existed)")
    print(f"Transcripts saved in: {OUTPUT_DIR}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
