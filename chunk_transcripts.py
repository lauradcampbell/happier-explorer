#!/usr/bin/env python3
"""
Process scraped transcripts into searchable chunks.

This reads the JSON files from the transcripts/ directory and produces
a single chunks.json file where each entry is a ~400-word passage
with metadata (episode title, date, guest, timestamp range).

Usage:
    python chunk_transcripts.py

Output:
    chunks.json — ready for embedding or keyword search
"""

import json
import re
from pathlib import Path


INPUT_DIR = Path("transcripts")
OUTPUT_FILE = Path("chunks.json")
CHUNK_TARGET_WORDS = 400
CHUNK_OVERLAP_WORDS = 50  # overlap between adjacent chunks for context continuity


# --- Ad/sponsor detection ---
# These patterns identify likely ad/sponsor segments to exclude
AD_INDICATORS = [
    "this episode is sponsored by",
    "this podcast is brought to you by",
    "brought to you by",
    "use code happier",
    "use the code happier",
    "sponsored by",
    "our sponsor",
    "today's sponsor",
    "terms and conditions may apply",
    "download the 10% happier app",
    "download the app",
    "sign up for dan's free newsletter",
    "head over to 10%",
    "danharris.com",
    "get the 10% with dan harris app",
    "blatant self-promotion",
    "follow dan on social",
    "subscribe to our youtube",
    "join wondery plus",
    "wondery plus subscribers",
    "amazon music",
    "apple podcasts",
    "listen early and ad free",
    "code happier at checkout",
    "meetfabric.com",
    "fastgrowingtrees.com",
    "linkedin.com/happier",
    "betterhelp.com",
    "function health",
]


def is_ad_segment(text: str) -> bool:
    """Heuristic: returns True if the segment looks like an ad or promo."""
    lower = text.lower()
    # Count how many ad indicators appear
    matches = sum(1 for indicator in AD_INDICATORS if indicator in lower)
    # If 2+ indicators, very likely an ad. If 1, check if it's a short segment.
    if matches >= 2:
        return True
    if matches == 1 and len(text.split()) < 150:
        return True
    return False


def extract_guest_name(title: str) -> str:
    """Try to extract guest name from episode title."""
    # Common patterns: "Topic | Guest Name" or "Topic with Guest Name"
    if "|" in title:
        parts = title.split("|")
        return parts[-1].strip()
    # "... with Guest Name" pattern
    match = re.search(r"\bwith\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", title)
    if match:
        return match.group(1)
    return ""


def chunk_segments(segments: list[dict], episode_meta: dict) -> list[dict]:
    """
    Combine segments into chunks of ~CHUNK_TARGET_WORDS words.
    Filter out ad segments.
    """
    # First, filter out ad segments
    clean_segments = []
    for seg in segments:
        if not is_ad_segment(seg["text"]):
            clean_segments.append(seg)

    if not clean_segments:
        return []

    chunks = []
    current_words = []
    current_timestamps = []
    word_count = 0

    for seg in clean_segments:
        words = seg["text"].split()
        current_words.extend(words)
        current_timestamps.append(seg["timestamp"])
        word_count += len(words)

        if word_count >= CHUNK_TARGET_WORDS:
            chunk_text = " ".join(current_words)
            chunks.append({
                "episode_title": episode_meta.get("title", ""),
                "episode_date": episode_meta.get("date", ""),
                "episode_slug": episode_meta.get("slug", ""),
                "guest": episode_meta.get("guest", ""),
                "timestamp_start": current_timestamps[0] if current_timestamps else "",
                "timestamp_end": current_timestamps[-1] if current_timestamps else "",
                "text": chunk_text,
                "word_count": len(current_words),
            })

            # Keep overlap for context continuity
            overlap_words = current_words[-CHUNK_OVERLAP_WORDS:]
            current_words = overlap_words
            current_timestamps = [current_timestamps[-1]] if current_timestamps else []
            word_count = len(overlap_words)

    # Don't forget the last chunk
    if current_words and len(current_words) > CHUNK_OVERLAP_WORDS:
        chunk_text = " ".join(current_words)
        chunks.append({
            "episode_title": episode_meta.get("title", ""),
            "episode_date": episode_meta.get("date", ""),
            "episode_slug": episode_meta.get("slug", ""),
            "guest": episode_meta.get("guest", ""),
            "timestamp_start": current_timestamps[0] if current_timestamps else "",
            "timestamp_end": current_timestamps[-1] if current_timestamps else "",
            "text": chunk_text,
            "word_count": len(current_words),
        })

    return chunks


def main():
    transcript_files = sorted(INPUT_DIR.glob("*.json"))
    print(f"Found {len(transcript_files)} transcript files in {INPUT_DIR}/")

    all_chunks = []
    episodes_processed = 0
    episodes_skipped = 0

    for tf in transcript_files:
        with open(tf, "r", encoding="utf-8") as f:
            episode = json.load(f)

        segments = episode.get("segments", [])
        if not segments:
            episodes_skipped += 1
            continue

        # Build episode metadata
        title = episode.get("title", tf.stem)
        meta = {
            "title": title,
            "date": episode.get("date", ""),
            "slug": episode.get("slug", tf.stem),
            "guest": extract_guest_name(title),
        }

        chunks = chunk_segments(segments, meta)
        all_chunks.extend(chunks)
        episodes_processed += 1

    # Assign unique IDs
    for i, chunk in enumerate(all_chunks):
        chunk["chunk_id"] = i

    # Save
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)

    print(f"\nProcessed: {episodes_processed} episodes")
    print(f"Skipped:   {episodes_skipped} episodes (no segments)")
    print(f"Total chunks: {len(all_chunks)}")
    print(f"Avg chunks per episode: {len(all_chunks) / max(episodes_processed, 1):.1f}")
    print(f"\nSaved to: {OUTPUT_FILE}")

    # Print some stats
    word_counts = [c["word_count"] for c in all_chunks]
    if word_counts:
        print(f"Chunk word counts: min={min(word_counts)}, max={max(word_counts)}, "
              f"avg={sum(word_counts)/len(word_counts):.0f}")


if __name__ == "__main__":
    main()
