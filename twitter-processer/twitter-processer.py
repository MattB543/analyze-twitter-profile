#!/usr/bin/env python3
"""twitter_to_llm.py

Combine parsing of Tweets, Likes, and Bookmarks exported from Twitter/X into
three plain‑text files (`tweets_for_llm.txt`, `likes_for_llm.txt`,
`bookmarks_for_llm.txt`) that are easy for large‑language models (LLMs) to consume.

Usage
-----
    python twitter_to_llm.py

The script will open a folder picker dialog. Select the folder containing your
Twitter data files, and the script will automatically find:
- tweets_*.jsonl (from Firefox extension or Twitter export)
- likes_*.jsonl (from Firefox extension or Twitter export)
- bookmarks_*.jsonl (from Firefox extension or Twitter export)

Output files are written to the same selected folder.

The script:
* reads JSONL files exported by the Firefox extension or Twitter export,
* converts each structure into a minimal, readable text representation,
* tries to include the parent tweet when you replied / quote‑tweeted, when that
  parent tweet is available in your likes file (handy context for an LLM).

Dependencies
------------
Python 3.10+ with the following external packages:
- requests (for URL metadata fetching)
- beautifulsoup4 (for HTML parsing)  
- google-genai (for image captioning via Gemini API)
- tkinter (for GUI folder picker - may not be available in headless environments)
"""

import json
import re
import sys
import tkinter as tk
import pathlib
import csv
import requests
import mimetypes
import hashlib
import time
import argparse
import urllib.parse
import socket
import ipaddress
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict, List
try:
    from google import genai
    from google.genai import types
except Exception as e:
    genai = None
    types = None
    _genai_import_error = e
from bs4 import BeautifulSoup

CLIENT = None

def get_client():
    """Get Gemini client, initializing on first call."""
    global CLIENT
    if CLIENT is None:
        if genai is None:
            raise RuntimeError(f"Google GenAI not available: {_genai_import_error}")
        CLIENT = genai.Client()  # reads GEMINI_API_KEY
    return CLIENT

# Regex for Twitter image URLs (only pbs.twimg.com URLs with query params)
# Note: We deliberately exclude t.co URLs from this regex because we rely on 
# media_mappings from entities data to resolve t.co -> pbs.twimg.com URLs.
# All parsers (tweets, likes, bookmarks) populate media_mappings from extended_entities.media
IMG_RE = re.compile(r"https://pbs\.twimg\.com/(?:media/\S+(?:\?format=(?:jpe?g|png|webp)|\.(?:jpe?g|png|webp))|amplify_video_thumb/\S+)")
# Regex for general external URLs (excluding Twitter image URLs, stop at common punctuation)
URL_RE = re.compile(r"https?://(?!pbs\.twimg\.com|t\.co)[^\s)\],>\"']+")

# Common file extensions to exclude (will filter these out separately)
EXCLUDE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.pdf', '.zip', '.tar', '.gz', '.rar', '.exe', '.dmg'}

# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #
def _load_js_array(file: Path, namespace: str) -> list:
    """Extract the JSON array assigned to *namespace* inside a Twitter export JS.

    Example *namespace* values:
        ``window.YTD.tweets.part0``  or  ``window.YTD.like.part0``
    """
    content = file.read_text(encoding="utf-8", errors="ignore")
    m = re.search(fr"{re.escape(namespace)}\s*=\s*(\[.*\])", content, re.DOTALL)
    if not m:
        raise ValueError(f"⚠️  Cannot find JSON payload in {file.name}")
    return json.loads(m.group(1))


def _clean_source(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _format_date(date_str: str) -> str:
    """Return 'YYYY‑MM‑DD HH:MM:SS' or the original string on failure."""
    try:
        dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:  # fall back
        print(f"⚠️  Failed to parse date '{date_str}': {e}", file=sys.stderr)
        return date_str


def find_files_in_folder(folder: Path) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    """Find tweets_*.jsonl, likes_*.jsonl, bookmarks_*.jsonl, and parents.json files in the given folder.
    
    Returns:
        Tuple of (tweets_file, likes_file, bookmarks_file, parents_file) or None if not found
    """
    tweets_file = None
    likes_file = None
    bookmarks_file = None
    parents_file = None
    
    # Look for tweets_*.jsonl (from Firefox extension)
    for file in folder.glob("tweets_*.jsonl"):
        tweets_file = file
        break  # Take the first one found
    
    # Look for likes_*.jsonl (from Firefox extension)
    for file in folder.glob("likes_*.jsonl"):
        likes_file = file
        break  # Take the first one found
    
    # Look for bookmarks_*.jsonl
    for file in folder.glob("bookmarks_*.jsonl"):
        bookmarks_file = file
        break  # Take the first one found
    
    # Look for parents.json
    parents_path = folder / "parents.json"
    if parents_path.exists():
        parents_file = parents_path
    
    return tweets_file, likes_file, bookmarks_file, parents_file


# --------------------------------------------------------------------------- #
#  Tweets & Likes                                                             #
# --------------------------------------------------------------------------- #

def parse_likes_jsonl(likes_file: Path) -> tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """Parse likes from JSONL file exported by Firefox extension and extract media/URL mappings.
    
    Returns:
        Tuple of (likes_lookup, media_mappings_dict, url_mappings_dict)
    """
    likes_lookup = {}
    all_media_mappings = {}
    all_url_mappings = {}
    
    for line_no, line in enumerate(likes_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            tweet_id = obj.get("tweet_id", "")
            
            # Extract media mappings and URL mappings from raw data
            raw_data = obj.get("raw", {})
            legacy = raw_data.get("legacy", {})
            
            # --- full text resolution order (same as tweets) ---
            text = None
            # 1) Note Tweet / Longform
            nt = raw_data.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
            if not text:
                text = nt.get("text")
                # note_tweet entity_set.urls[]
                for u in nt.get("entity_set", {}).get("urls", []):
                    short = u.get("url")
                    exp = u.get("expanded_url")
                    if short and exp:
                        all_url_mappings[short] = exp

            # 2) Legacy full_text
            if not text:
                text = legacy.get("full_text") or legacy.get("text")

            # 3) Fallback to top-level truncated
            if not text:
                text = obj.get("text", "")

            # Pull URL mappings from legacy.entities.urls
            entities = legacy.get("entities", {})
            for u in entities.get("urls", []):
                short = u.get("url")
                exp = u.get("expanded_url")
                if short and exp:
                    all_url_mappings[short] = exp
            
            if tweet_id and text:
                likes_lookup[tweet_id] = text

            if raw_data:
                extended_entities = legacy.get("extended_entities", {})
                
                # Extract media URL mappings
                media_source = extended_entities.get("media", []) or entities.get("media", [])
                for media_data in media_source:
                    shortened = media_data.get("url", "")
                    media_url = media_data.get("media_url_https", "")
                    if shortened and media_url:
                        # Store media mapping but don't expand in text - leave for image processing
                        all_media_mappings[shortened] = media_url
                        
        except json.JSONDecodeError:
            print(f"⚠️  Skipping malformed JSON on line {line_no} in {likes_file.name}", file=sys.stderr)
            continue
    return likes_lookup, all_media_mappings, all_url_mappings


def load_parents_json(parents_file: Path) -> tuple[Dict[str, str], Dict[str, str]]:
    """Load parent tweets from parents.json and convert to tweet_id -> text mapping.
    
    Returns:
        Tuple of (parent_lookup, parent_url_mappings)
    """
    try:
        with parents_file.open('r', encoding='utf-8') as f:
            parents_data = json.load(f)
        
        # Convert Twitter API v2 format to our lookup format
        parent_lookup = {}
        parent_url_mappings = {}
        
        for tweet_id, tweet_data in parents_data.items():
            # Extract text from Twitter API v2 response format
            text = tweet_data.get('text', '')
            if text:
                parent_lookup[tweet_id] = text
            
            # Extract URL mappings from entities when available
            entities = tweet_data.get('entities', {})
            for url_entity in entities.get('urls', []):
                short = url_entity.get('url', '')
                expanded = url_entity.get('expanded_url', '')
                if short and expanded:
                    parent_url_mappings[short] = expanded
        
        print(f"📖  Loaded {len(parent_lookup)} parent tweets from {parents_file.name}")
        if parent_url_mappings:
            print(f"📖  Extracted {len(parent_url_mappings)} URL mappings from parent tweets")
        return parent_lookup, parent_url_mappings
        
    except Exception as e:
        print(f"⚠️  Failed to load parent tweets: {e}")
        return {}, {}


def parse_tweets_jsonl(tweets_file: Path) -> tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, str]]:
    """Parse tweets from JSONL file exported by Firefox extension.
    
    Returns:
        Tuple of (tweets_list, media_mappings_dict, url_mappings_dict)
    """
    tweets = []
    all_media_mappings = {}
    all_url_mappings = {}
    
    for line_no, line in enumerate(tweets_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            
            # Extract proper parent relationships from raw data (more reliable than parent_ids array)
            raw_data = obj.get("raw", {})
            legacy = raw_data.get("legacy", {})
            
            # --- full text resolution order ---
            text = None
            # 1) Note Tweet / Longform
            nt = raw_data.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
            if not text:
                text = nt.get("text")
                # note_tweet entity_set.urls[]
                for u in nt.get("entity_set", {}).get("urls", []):
                    short = u.get("url")
                    exp = u.get("expanded_url")
                    if short and exp:
                        all_url_mappings[short] = exp

            # 2) Legacy full_text
            if not text:
                text = legacy.get("full_text") or legacy.get("text")

            # 3) Fallback to top-level truncated
            if not text:
                text = obj.get("text", "")

            # Pull URL mappings from legacy.entities.urls
            entities = legacy.get("entities", {})
            for u in entities.get("urls", []):
                short = u.get("url")
                exp = u.get("expanded_url")
                if short and exp:
                    all_url_mappings[short] = exp
            
            # Get reply and quote IDs from the authoritative sources
            reply_to_tweet_id = legacy.get("in_reply_to_status_id_str", "")
            quoted_tweet_id = legacy.get("quoted_status_id_str", "")
            
            # Check for retweet - look for retweeted_status_result, not retweet count
            is_retweet = bool(raw_data.get("retweeted_status_result"))
            
            # Media mappings (legacy.extended_entities.media preferred)
            extended_entities = legacy.get("extended_entities", {})
            media_source = extended_entities.get("media", []) or entities.get("media", [])
            for media_data in media_source:
                shortened = media_data.get("url", "")
                media_url = media_data.get("media_url_https", "")
                if shortened and media_url:
                    all_media_mappings[shortened] = media_url
            
            # Fallback to parent_ids if raw data not available
            if not reply_to_tweet_id and not quoted_tweet_id:
                parent_ids = obj.get("parent_ids", [])
                if parent_ids:
                    # Try to guess: if reply_count > 0, first parent_id is likely reply
                    if obj.get("reply", 0) > 0 and parent_ids:
                        reply_to_tweet_id = parent_ids[0]
                    elif obj.get("quote", 0) > 0 and parent_ids:
                        quoted_tweet_id = parent_ids[0]
                    elif len(parent_ids) > 1:
                        reply_to_tweet_id = parent_ids[0]
                        quoted_tweet_id = parent_ids[1]
                    elif parent_ids:
                        # Single parent - could be either, default to reply
                        reply_to_tweet_id = parent_ids[0]
            
            tweets.append({
                "id": obj.get("tweet_id", ""),
                "created_at": obj.get("created_at", ""),
                "text": text or "",
                "is_retweet": is_retweet,
                "is_reply": bool(reply_to_tweet_id),
                "quoted_tweet_id": quoted_tweet_id,
                "reply_to_tweet_id": reply_to_tweet_id,
                "reply_to_user": legacy.get("in_reply_to_screen_name", ""),
            })
        except json.JSONDecodeError:
            print(f"⚠️  Skipping malformed JSON on line {line_no} in {tweets_file.name}", file=sys.stderr)
            continue
    return tweets, all_media_mappings, all_url_mappings


def get_thread_context(tweet_id: str, tweet_lookup: Dict[str, str], max_depth: int = 3, visited: set = None) -> List[str]:
    """
    Get thread context for a tweet, following parent relationships up to max_depth.
    
    Args:
        tweet_id: The starting tweet ID
        tweet_lookup: Dictionary mapping tweet IDs to tweet content
        max_depth: Maximum depth to traverse (default 3 to prevent infinite chains)
        visited: Set of already visited tweet IDs to prevent cycles
    
    Returns:
        List of tweet texts in chronological order (oldest first)
    """
    if visited is None:
        visited = set()
    
    if tweet_id in visited or max_depth <= 0:
        return []
    
    visited.add(tweet_id)
    context = []
    
    # Get the current tweet content
    current_content = tweet_lookup.get(tweet_id, "")
    if current_content:
        context.append(current_content)
    
    # Note: For deeper thread context, we'd need parent relationships
    # The current implementation is already limited to 1 level, which is good
    
    return context


def export_tweets_text(tweets: List[Dict[str, Any]],
                       tweet_lookup: Dict[str, str],
                       outfile: Path,
                       url_to_caption: Dict[str, str] = None,
                       url_to_meta: Dict[str, str] = None,
                       url_mappings: Dict[str, str] = None) -> None:
    with outfile.open("w", encoding="utf-8") as f:
        last = len(tweets) - 1
        for i, tw in enumerate(tweets):
            # Add context for replies / quotes if we have it (limit to prevent long chains)
            context_depth = 0
            max_context_depth = 3
            
            if tw["is_reply"] and tw["reply_to_tweet_id"] and context_depth < max_context_depth:
                original = tweet_lookup.get(tw["reply_to_tweet_id"], "")
                if original:
                    # Truncate very long context tweets to keep output manageable
                    if len(original) > 500:
                        original = original[:500] + "... [truncated]"
                    
                    if url_mappings:
                        original = expand_short_urls(original, url_mappings)
                    if url_to_caption:
                        original = replace_images_with_captions(original, url_to_caption)
                    if url_to_meta:
                        original = replace_urls_with_meta(original, url_to_meta)
                    f.write(f"Original (@{tw['reply_to_user']}):\n{original}\n\nMy Reply:\n")
                    context_depth += 1
                    
            elif tw["quoted_tweet_id"] and context_depth < max_context_depth:
                original = tweet_lookup.get(tw["quoted_tweet_id"], "")
                if original:
                    # Truncate very long context tweets to keep output manageable
                    if len(original) > 500:
                        original = original[:500] + "... [truncated]"
                    
                    if url_mappings:
                        original = expand_short_urls(original, url_mappings)
                    if url_to_caption:
                        original = replace_images_with_captions(original, url_to_caption)
                    if url_to_meta:
                        original = replace_urls_with_meta(original, url_to_meta)
                    f.write(f"Quoted tweet:\n{original}\n\nQuote:\n")
                    context_depth += 1

            text = tw["text"].replace("\r", "")
            if url_mappings:
                text = expand_short_urls(text, url_mappings)
            if url_to_caption:
                text = replace_images_with_captions(text, url_to_caption)
            if url_to_meta:
                text = replace_urls_with_meta(text, url_to_meta)
            f.write(text)
            if i != last:
                f.write("\n---\n")


def export_likes_text(tweet_lookup: Dict[str, str], outfile: Path, url_to_caption: Dict[str, str] = None, url_to_meta: Dict[str, str] = None, url_mappings: Dict[str, str] = None) -> None:
    with outfile.open("w", encoding="utf-8") as f:
        ids = list(tweet_lookup)
        last = len(ids) - 1
        for i, tid in enumerate(ids):
            text = tweet_lookup[tid].replace("\r", "")
            if url_mappings:
                text = expand_short_urls(text, url_mappings)
            if url_to_caption:
                text = replace_images_with_captions(text, url_to_caption)
            if url_to_meta:
                text = replace_urls_with_meta(text, url_to_meta)
            f.write(text)
            if i != last:
                f.write("\n---\n")


# --------------------------------------------------------------------------- #
#  Bookmarks                                                                  #
# --------------------------------------------------------------------------- #

def parse_bookmarks_jsonl(bookmarks_file: Path) -> tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, str]]:
    """Parse bookmarks from JSONL file - handles both old and new formats.
    
    Returns:
        Tuple of (tweets_list, media_mappings_dict, url_mappings_dict)
    """
    tweets = []
    all_media_mappings = {}
    all_url_mappings = {}
    
    for line_no, line in enumerate(bookmarks_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(f"⚠️  Skipping malformed JSON on line {line_no}", file=sys.stderr)
            continue
        
        # Check if this is the new simplified format from Firefox extension
        if "tweet_id" in obj and "text" in obj:
            # New format - extract media and URL mappings from raw data if available
            raw_data = obj.get("raw", {})
            if raw_data:
                legacy = raw_data.get("legacy", {})
                
                # --- full text resolution order (same as tweets/likes) ---
                text = None
                # 1) Note Tweet / Longform
                nt = raw_data.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
                if not text:
                    text = nt.get("text")
                    # note_tweet entity_set.urls[]
                    for u in nt.get("entity_set", {}).get("urls", []):
                        short = u.get("url")
                        exp = u.get("expanded_url")
                        if short and exp:
                            all_url_mappings[short] = exp

                # 2) Legacy full_text
                if not text:
                    text = legacy.get("full_text") or legacy.get("text")

                # 3) Fallback to top-level 
                if not text:
                    text = obj.get("text", "")
                
                # Extract URL mappings from legacy data
                entities = legacy.get("entities", {})
                for u in entities.get("urls", []):
                    short = u.get("url")
                    exp = u.get("expanded_url")
                    if short and exp:
                        all_url_mappings[short] = exp
                
                extended_entities = legacy.get("extended_entities", {})
                
                # Extract media URL mappings
                media_source = extended_entities.get("media", []) or entities.get("media", [])
                for media_data in media_source:
                    shortened = media_data.get("url", "")
                    media_url = media_data.get("media_url_https", "")
                    if shortened and media_url:
                        # Store media mapping but don't expand in text - leave for image processing
                        all_media_mappings[shortened] = media_url
            else:
                text = obj.get("text", "")
            
            tweets.append({
                "screen_name": "",  # Not available in new format
                "full_text": text,
            })
            continue
        
        # Old format - complex parsing from Twitter export
        # Extract screen name from the nested structure
        screen_name = ""
        raw_data = obj.get("raw", {})
        if raw_data:
            core_data = raw_data.get("core", {})
            user_results = core_data.get("user_results", {})
            result = user_results.get("result", {})
            core_user = result.get("core", {})
            screen_name = core_user.get("screen_name", "")
        
        # If no screen name found in raw data, fall back to top-level user
        if not screen_name:
            user = obj.get("user", {})
            screen_name = user.get("screen_name", "")
        
        # Extract full text and URL mappings - check for note tweet first, then legacy full_text
        full_text = ""
        url_mappings = {}  # For regular URLs (not media)
        media_mappings = {}  # For media URLs (t.co -> pbs.twimg.com)
        text_source = None  # Track which source we used for text
        
        if raw_data:
            # Check for note tweet (long-form content)
            note_tweet = raw_data.get("note_tweet", {})
            if note_tweet:
                note_results = note_tweet.get("note_tweet_results", {})
                note_result = note_results.get("result", {})
                note_text = note_result.get("text", "")
                if note_text:
                    full_text = note_text
                    text_source = "note_tweet"
                    # Extract URL mappings from note_tweet
                    entity_set = note_result.get("entity_set", {})
                    urls = entity_set.get("urls", [])
                    for url_data in urls:
                        shortened = url_data.get("url", "")
                        expanded = url_data.get("expanded_url", "")
                        if shortened and expanded:
                            url_mappings[shortened] = expanded
            
            # Fall back to legacy full_text if no note tweet
            if not full_text:
                legacy = raw_data.get("legacy", {})
                legacy_text = legacy.get("full_text", "")
                if legacy_text:
                    full_text = legacy_text
                    text_source = "legacy"
                    # Extract URL mappings from legacy (non-media only)
                    entities = legacy.get("entities", {})
                    urls = entities.get("urls", [])
                    for url_data in urls:
                        shortened = url_data.get("url", "")
                        expanded = url_data.get("expanded_url", "")
                        if shortened and expanded:
                            url_mappings[shortened] = expanded
                
                # Extract media URL mappings separately (don't expand these in text)
                extended_entities = legacy.get("extended_entities", {})
                media_source = extended_entities.get("media", []) or entities.get("media", [])
                for media_data in media_source:
                    shortened = media_data.get("url", "")
                    media_url = media_data.get("media_url_https", "")
                    if shortened and media_url:
                        # Store media mapping but don't expand in text - leave for image processing
                        media_mappings[shortened] = media_url
        
        # If no text found in raw data, fall back to top-level text
        if not full_text:
            full_text = obj.get("text", "")
        
        # Replace shortened URLs with expanded URLs (but NOT media URLs)
        for shortened, expanded in url_mappings.items():
            full_text = full_text.replace(shortened, expanded)
        
        # Merge URL mappings into function-level mappings for downstream use
        all_url_mappings.update(url_mappings)
        
        # Collect media mappings for image processing
        all_media_mappings.update(media_mappings)
        
        tweets.append({
            "screen_name": '@' + screen_name if screen_name else "",
            "full_text": full_text,
        })
    return tweets, all_media_mappings, all_url_mappings


def export_bookmarks_text(bookmarks: List[Dict[str, Any]], outfile: Path, url_to_caption: Dict[str, str] = None, url_to_meta: Dict[str, str] = None, url_mappings: Dict[str, str] = None) -> None:
    with outfile.open("w", encoding="utf-8") as f:
        last = len(bookmarks) - 1
        for i, tw in enumerate(bookmarks):
            screen_name = tw.get("screen_name", "")
            full_text = tw.get("full_text", "").replace("\r", "")
            
            if url_mappings:
                full_text = expand_short_urls(full_text, url_mappings)
            if url_to_caption:
                full_text = replace_images_with_captions(full_text, url_to_caption)
            if url_to_meta:
                full_text = replace_urls_with_meta(full_text, url_to_meta)
            
            if screen_name:
                f.write(f"{screen_name}:\n")
            f.write(full_text)
            if i != last:
                f.write("\n---\n")


# --------------------------------------------------------------------------- #
#  URL Metadata Extraction                                                    #
# --------------------------------------------------------------------------- #

def should_fetch_url(url: str, allow_domains: set = None) -> bool:
    """Check if a URL is safe to fetch (SSRF protection)."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        
        hostname = parsed.hostname
        if not hostname:
            return False
            
        # Check domain allowlist if provided
        if allow_domains and hostname not in allow_domains:
            return False
        
        # Resolve hostname to IP and check for private ranges
        try:
            ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
                return False
        except (socket.gaierror, ValueError):
            # If we can't resolve, let it through (will fail on request)
            pass
            
        return True
    except Exception:
        return False


def fetch_url_metadata(url: str, max_retries: int = 3, allow_domains: set = None) -> Dict[str, str]:
    """Fetch meta title and description from a URL with retry logic.
    
    Args:
        url: URL to fetch metadata from
        max_retries: Maximum number of retry attempts
        allow_domains: Set of allowed domains (optional)
    
    Returns:
        Dictionary with 'title' and 'description' keys
    """
    # Safety check
    if not should_fetch_url(url, allow_domains):
        return {
            'title': f"ERROR: URL blocked for security reasons",
            'description': ""
        }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            
            # Handle rate limiting with exponential backoff
            if response.status_code == 429:
                if attempt < max_retries:
                    wait_time = (2 ** attempt) * 1  # 1, 2, 4 seconds
                    print(f"⏳ Rate limited on {url}, waiting {wait_time}s (attempt {attempt + 1}/{max_retries + 1})")
                    time.sleep(wait_time)
                    continue
                else:
                    return {
                        'title': f"ERROR: Rate limited after {max_retries} retries",
                        'description': ""
                    }
            
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract title
            title = ""
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text().strip()
            
            # Extract meta description
            description = ""
            desc_tag = soup.find('meta', attrs={'name': 'description'})
            if not desc_tag:
                desc_tag = soup.find('meta', attrs={'property': 'og:description'})
            if desc_tag:
                description = desc_tag.get('content', '').strip()
            
            return {
                'title': title,
                'description': description
            }
            
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                wait_time = (2 ** attempt) * 1  # 1, 2, 4 seconds
                print(f"⚠️  Request failed for {url}, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries + 1}): {e}")
                time.sleep(wait_time)
                continue
            else:
                return {
                    'title': f"ERROR: {e}",
                    'description': ""
                }
        except Exception as e:
            return {
                'title': f"ERROR: {e}",
                'description': ""
            }
    
    # Should not reach here, but just in case
    return {
        'title': "ERROR: Unexpected failure",
        'description': ""
    }


def generate_url_metadata_from_texts(texts: List[str], max_urls: int = 1000, allow_domains: set = None) -> Dict[str, str]:
    """Generate metadata for all external URLs found in the given texts.
    
    Args:
        texts: List of text content to scan for URLs
        max_urls: Maximum number of URLs to process (default 1000)
        allow_domains: Set of allowed domains for fetching (optional)
    
    Returns:
        Dictionary mapping URLs to their enhanced format with title and description
    """
    url_to_meta = {}
    all_urls = set()
    
    # Collect all unique external URLs from all texts
    for text in texts:
        urls = URL_RE.findall(text)
        # Filter out URLs with excluded file extensions
        filtered_urls = []
        for url in urls:
            # Check if URL ends with any excluded extension
            url_lower = url.lower()
            is_excluded = any(url_lower.endswith(ext) for ext in EXCLUDE_EXTENSIONS)
            if not is_excluded:
                filtered_urls.append(url)
        all_urls.update(filtered_urls)
    
    # Limit the number of URLs to process
    urls_to_process = list(all_urls)[:max_urls]
    if len(all_urls) > max_urls:
        print(f"⚠️  Found {len(all_urls)} URLs, limiting to first {max_urls} for processing")
    
    # Generate metadata for each unique URL
    for i, url in enumerate(urls_to_process, 1):
        try:
            metadata = fetch_url_metadata(url, allow_domains=allow_domains)
            title = metadata['title']
            description = metadata['description']
            
            # Create the enhanced format: URL (title - description)
            if title and description:
                enhanced = f"{url} ({title} - {description})"
            elif title:
                enhanced = f"{url} ({title})"
            else:
                enhanced = url  # Keep original if no metadata found
            
            url_to_meta[url] = enhanced
            print(f"✅  [{i}/{len(urls_to_process)}] Generated metadata for {url}")
        except Exception as e:
            url_to_meta[url] = url  # Keep original on error
            print(f"❌  [{i}/{len(urls_to_process)}] Failed to get metadata for {url}: {e}")
    
    return url_to_meta


def expand_short_urls(text: str, url_mappings: Dict[str, str]) -> str:
    """Expand shortened URLs in text using the URL mappings."""
    for short, expanded in url_mappings.items():
        text = text.replace(short, expanded)
    return text


def replace_urls_with_meta(text: str, url_to_meta: Dict[str, str]) -> str:
    """Replace URLs in text with enhanced format including title and description."""
    for url, enhanced in url_to_meta.items():
        if url in text:
            text = text.replace(url, enhanced)
    return text


def save_url_metadata_csv(url_to_meta: Dict[str, str], out_path: Path):
    """Save URL to metadata mappings as CSV."""
    metadata_rows = []
    for original_url, enhanced in url_to_meta.items():
        # Extract title and description from enhanced format
        if " (" in enhanced and enhanced.endswith(")"):
            meta_part = enhanced[enhanced.rfind(" (") + 2:-1]
            if " - " in meta_part:
                title, description = meta_part.split(" - ", 1)
            else:
                title = meta_part
                description = ""
        else:
            title = ""
            description = ""
        
        metadata_rows.append({
            "url": original_url,
            "title": title,
            "description": description,
            "enhanced": enhanced
        })
    
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, ["url", "title", "description", "enhanced"])
        writer.writeheader()
        writer.writerows(metadata_rows)


# --------------------------------------------------------------------------- #
#  Image Captioning                                                           #
# --------------------------------------------------------------------------- #

def describe_image(url: str, media_mappings: Dict[str, str] = None, cache_dir: Path = None, prompt="""You are an expert image analyst creating a summary for a language model that is analyzing social media posts. Your summary must be a single, dense paragraph.

Prioritize in this order:
1.  **Key Text:** Extract the most important text (titles, headlines, key phrases in a meme, data labels on a graph). This is the most critical information.
2.  **Image Type & Subject:** Identify the type of image (e.g., screenshot of an article, infographic, meme, photograph, book cover) and its main subject.
3.  **Core Message or Event:** What is the central message, action, or event? What is the main takeaway at a glance?
4.  **Key Entities:** Mention any important people, products, or organizations shown.

AVOID describing:
- Colors, fonts, or specific layout details (e.g., "two-column layout", "serif font").
- Minor background elements or artistic style unless it's the main subject.
- Do not begin with "This image shows..." or "The picture depicts...".
"""):
    # Resolve t.co URLs to actual image URLs
    actual_image_url = url
    if media_mappings and url in media_mappings:
        actual_image_url = media_mappings[url]
    elif url.startswith("https://t.co/") and not media_mappings:
        # If it's a t.co URL but we don't have mappings, we can't process it
        return f"ERROR: Cannot resolve t.co URL {url} without media mappings"
    
    # Download image with proper validation
    try:
        response = requests.get(actual_image_url, timeout=15)
        response.raise_for_status()  # Raise exception for 4xx/5xx status codes
        
        # Verify it's actually an image
        content_type = response.headers.get('Content-Type', '').lower()
        if not content_type.startswith('image/'):
            return f"ERROR: URL returned {content_type}, not an image"
        
        img_bytes = response.content
        if len(img_bytes) == 0:
            return "ERROR: Empty response from image URL"
            
    except requests.exceptions.RequestException as e:
        return f"ERROR: Failed to download image: {e}"
    
    img_hash = hashlib.sha1(img_bytes).hexdigest()
    
    # Check cache first if cache_dir provided
    if cache_dir:
        cache_file = cache_dir / f"{img_hash}.txt"
        if cache_file.exists():
            try:
                return cache_file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"⚠️  Failed to read cached caption for {img_hash}: {e}", file=sys.stderr)
                # Fall through to generate new caption
    
    # Generate new caption
    mime = mimetypes.guess_type(actual_image_url)[0] or "image/jpeg"
    client = get_client()
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type=mime),
            prompt
        ],
    )
    caption = resp.text
    
    # Save to cache if cache_dir provided
    if cache_dir:
        try:
            cache_dir.mkdir(exist_ok=True)
            cache_file = cache_dir / f"{img_hash}.txt"
            cache_file.write_text(caption, encoding="utf-8")
        except Exception as e:
            print(f"⚠️  Failed to cache caption for {img_hash}: {e}")
    
    return caption


def generate_image_captions_from_texts(texts: List[str], media_mappings: Dict[str, str] = None, cache_dir: Path = None, max_images: int = 500) -> Dict[str, str]:
    """Generate captions for all images found in the given texts.
    
    Args:
        texts: List of text content to scan for image URLs
        media_mappings: Dictionary mapping t.co URLs to actual image URLs
        cache_dir: Optional directory for caching captions by image hash
        max_images: Maximum number of images to process (default 500)
    
    Returns:
        Dictionary mapping image URLs to their captions
    """
    url_to_caption = {}
    all_urls = set()
    
    # Collect all unique image URLs from all texts (regex grab direct pbs image/video thumb URLs)
    for text in texts:
        urls = IMG_RE.findall(text)
        all_urls.update(urls)
    
    # ALWAYS include short media URLs we learned from Entities
    if media_mappings:
        all_urls.update(media_mappings.keys())
    
    if not all_urls:
        return url_to_caption
    
    # Limit the number of images to process
    urls_to_process = list(all_urls)[:max_images]
    if len(all_urls) > max_images:
        print(f"⚠️  Found {len(all_urls)} images, limiting to first {max_images} for processing")
        
    print(f"🖼️  Processing {len(urls_to_process)} unique images for captioning")
    if cache_dir:
        print(f"💾  Using image caption cache: {cache_dir}")
    if media_mappings:
        print(f"🔗  Using media mappings for {len(media_mappings)} t.co URLs")
    
    # Generate captions for each unique URL
    for i, url in enumerate(urls_to_process, 1):
        # Skip t.co URLs that we don't have mappings for
        if url.startswith("https://t.co/") and (not media_mappings or url not in media_mappings):
            print(f"⏭️  [{i}/{len(urls_to_process)}] Skipping unknown t.co URL: {url}")
            continue
            
        try:
            caption = describe_image(url, media_mappings, cache_dir)
            url_to_caption[url] = caption
            print(f"✅  [{i}/{len(urls_to_process)}] Generated caption: {url[:50]}...")
        except Exception as e:
            caption = f"ERROR: {e}"
            url_to_caption[url] = caption
            print(f"❌  [{i}/{len(urls_to_process)}] Failed to caption {url[:50]}...: {e}")
    
    return url_to_caption


def replace_images_with_captions(text: str, url_to_caption: Dict[str, str]) -> str:
    """Replace image URLs in text with [image](caption) format."""
    for url, caption in url_to_caption.items():
        if url in text:
            text = text.replace(url, f"[image]({caption})")
    return text


def save_captions_csv(url_to_caption: Dict[str, str], out_path: Path):
    """Save image URL to caption mappings as CSV."""
    captions = [{"url": url, "caption": caption} for url, caption in url_to_caption.items()]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, ["url", "caption"])
        writer.writeheader()
        writer.writerows(captions)


def gen_captions(bookmarks_path: pathlib.Path, out_path: pathlib.Path):
    """Legacy function for backwards compatibility - generates captions from already-written file."""
    captions = []
    for line in bookmarks_path.read_text(encoding="utf-8").split("---"):
        for url in IMG_RE.findall(line):
            try:
                captions.append({"url": url,
                                 "caption": describe_image(url)})
            except Exception as e:
                captions.append({"url": url, "caption": f"ERROR: {e}"})
    # write a simple CSV the LLM can ingest
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, ["url", "caption"])
        writer.writeheader()
        writer.writerows(captions)


# --------------------------------------------------------------------------- #
#  CLI                                                                        #
# --------------------------------------------------------------------------- #

def safe_messagebox(message_type: str, title: str, message: str) -> None:
    """Show message via GUI or print to console in headless environments."""
    try:
        if message_type == "error":
            messagebox.showerror(title, message)
        elif message_type == "info":
            messagebox.showinfo(title, message)
        else:
            messagebox.showinfo(title, message)
    except Exception:
        # Fallback to console output in headless environments
        print(f"{title}: {message}")


def get_folder_path() -> Path:
    """Get folder path via GUI or CLI arguments, with fallback for headless environments."""
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Process Twitter data for LLM consumption")
    parser.add_argument("--folder", type=str, help="Path to folder containing Twitter data files")
    args = parser.parse_args()
    
    # If folder provided via CLI, use it
    if args.folder:
        folder = Path(args.folder)
        if not folder.exists() or not folder.is_dir():
            print(f"❌  Invalid folder path: {folder}")
            sys.exit(1)
        print(f"📁  Using folder from CLI: {folder}")
        return folder
    
    # Try GUI folder picker (may fail in headless environments)
    try:
        root = tk.Tk()
        root.withdraw()
        
        folder_path = filedialog.askdirectory(
            title="Select folder containing Twitter export files"
        )
        
        if not folder_path:
            print("❌  No folder selected. Exiting.")
            sys.exit(1)
            
        folder = Path(folder_path)
        print(f"📁  Selected folder: {folder}")
        return folder
        
    except tk.TclError as e:
        # Specific handling for display issues in headless environments
        print(f"⚠️  GUI not available (no display): {e}")
        print("💡  Use --folder argument to specify path, e.g.:")
        print(f"     python {sys.argv[0]} --folder /path/to/twitter/data")
    except Exception as e:
        # Fallback for other GUI issues
        print(f"⚠️  GUI not available: {e}")
        print("💡  Use --folder argument to specify path, e.g.:")
        print(f"     python {sys.argv[0]} --folder /path/to/twitter/data")
        
        # Prompt for input as last resort
        try:
            folder_input = input("📁  Enter folder path: ").strip()
            if not folder_input:
                print("❌  No folder provided. Exiting.")
                sys.exit(1)
                
            folder = Path(folder_input)
            if not folder.exists() or not folder.is_dir():
                print(f"❌  Invalid folder path: {folder}")
                sys.exit(1)
                
            print(f"📁  Using folder: {folder}")
            return folder
            
        except (KeyboardInterrupt, EOFError):
            print("\n❌  Cancelled. Exiting.")
            sys.exit(1)


def main() -> None:
    folder = get_folder_path()
    
    # Find the required files
    tweets_file, likes_file, bookmarks_file, parents_file = find_files_in_folder(folder)
    
    # Check what files we found
    found_files = []
    missing_files = []
    
    if tweets_file:
        found_files.append(f"✅  Found tweets file: {tweets_file.name}")
    else:
        missing_files.append("⚠️  No tweets_*.jsonl file found")
    
    if likes_file:
        found_files.append(f"✅  Found likes file: {likes_file.name}")
    else:
        missing_files.append("⚠️  No likes_*.jsonl file found")
    
    if bookmarks_file:
        found_files.append(f"✅  Found bookmarks file: {bookmarks_file.name}")
    else:
        missing_files.append("⚠️  No bookmarks_*.jsonl file found")
    
    if parents_file:
        found_files.append(f"✅  Found parents file: {parents_file.name}")
    else:
        missing_files.append("ℹ️  No parents.json file found (optional - run hydrate_parents.py first)")
    
    # Print status
    for msg in found_files:
        print(msg)
    for msg in missing_files:
        print(msg)
    
    # Check if we have at least one data file
    if not tweets_file and not likes_file and not bookmarks_file:
        error_msg = "Cannot proceed without at least one of: tweets_*.jsonl, likes_*.jsonl, or bookmarks_*.jsonl files."
        print(f"❌  {error_msg}")
        safe_messagebox("error", "Missing Files", error_msg)
        return
    
    # Process data and collect all texts for image analysis
    try:
        like_lookup = {}
        tweets = []
        all_media_mappings = {}  # Collect media mappings from all sources
        
        all_url_mappings = {}
        if likes_file:
            print("\n🔄  Processing likes...")
            like_lookup, likes_media_mappings, likes_url_mappings = parse_likes_jsonl(likes_file)
            all_media_mappings.update(likes_media_mappings)
            all_url_mappings.update(likes_url_mappings)
        
        if tweets_file:
            print("🔄  Processing tweets...")
            tweets, tweets_media_mappings, tweets_url_mappings = parse_tweets_jsonl(tweets_file)
            all_media_mappings.update(tweets_media_mappings)
            all_url_mappings.update(tweets_url_mappings)
        
        # Load parent tweets if available (keep separate to avoid overwriting likes)
        parent_lookup = {}
        if parents_file:
            print("🔄  Loading parent tweets...")
            parent_lookup, parent_url_mappings = load_parents_json(parents_file)
            all_url_mappings.update(parent_url_mappings)
            print(f"📖  Total tweets in lookup: {len(like_lookup)} likes + {len(parent_lookup)} parents")
        
        # Build self tweet lookup so replies/quotes to your own tweets resolve
        self_tweet_lookup = {}
        if tweets:
            self_tweet_lookup = {tw["id"]: tw["text"] for tw in tweets if tw.get("id")}
        
        # Helper function to lookup tweets with precedence: likes first, then parents, then self
        def lookup_tweet(tweet_id):
            """Look up tweet text, preferring likes over parents over self tweets."""
            if tweet_id in like_lookup:
                return like_lookup[tweet_id]
            elif tweet_id in parent_lookup:
                return parent_lookup[tweet_id]
            elif tweet_id in self_tweet_lookup:
                return self_tweet_lookup[tweet_id]
            return None
        
        all_texts = []
        
        # Collect tweet texts for image analysis (expand URLs first)
        for tw in tweets:
            expanded_text = expand_short_urls(tw["text"], all_url_mappings)
            all_texts.append(expanded_text)
            # Also include quoted/replied-to tweets if available
            if tw["reply_to_tweet_id"]:
                parent_text = lookup_tweet(tw["reply_to_tweet_id"])
                if parent_text:
                    expanded_parent_text = expand_short_urls(parent_text, all_url_mappings)
                    all_texts.append(expanded_parent_text)
            if tw["quoted_tweet_id"]:
                quoted_text = lookup_tweet(tw["quoted_tweet_id"])
                if quoted_text:
                    expanded_quoted_text = expand_short_urls(quoted_text, all_url_mappings)
                    all_texts.append(expanded_quoted_text)
        
        # Collect liked tweet texts (expand URLs)
        for liked_text in like_lookup.values():
            expanded_liked_text = expand_short_urls(liked_text, all_url_mappings)
            all_texts.append(expanded_liked_text)
        
        # Also collect parent tweet texts (that aren't already in likes)
        for parent_id, parent_text in parent_lookup.items():
            if parent_id not in like_lookup:
                expanded_parent_text = expand_short_urls(parent_text, all_url_mappings)
                all_texts.append(expanded_parent_text)
        
        # Process bookmarks if available
        bookmarks = None
        if bookmarks_file:
            print("🔄  Processing bookmarks...")
            bookmarks, bookmarks_media_mappings, bookmarks_url_mappings = parse_bookmarks_jsonl(bookmarks_file)
            all_media_mappings.update(bookmarks_media_mappings)
            all_url_mappings.update(bookmarks_url_mappings)
            # Add bookmark texts to analysis (expand URLs)
            for bm in bookmarks:
                bookmark_text = bm.get("full_text", "")
                expanded_bookmark_text = expand_short_urls(bookmark_text, all_url_mappings)
                all_texts.append(expanded_bookmark_text)
        
        # Generate image captions for all texts with caching
        url_to_caption = {}
        try:
            print("🔄  Generating image captions...")
            image_cache_dir = folder / "image_cache"
            url_to_caption = generate_image_captions_from_texts(all_texts, all_media_mappings, image_cache_dir)
            if url_to_caption:
                # Save the image URL to caption mappings as CSV
                save_captions_csv(url_to_caption, folder / "image_captions.csv")
                print("✅  Generated image captions and exported image_captions.csv")
            else:
                print("ℹ️  No images found in the texts")
        except Exception as e:
            print(f"⚠️  Failed generating image captions: {e}")
        
        # Generate URL metadata for all texts
        url_to_meta = {}
        try:
            print("🔄  Generating URL metadata...")
            url_to_meta = generate_url_metadata_from_texts(all_texts)
            if url_to_meta:
                # Save the URL to metadata mappings as CSV
                save_url_metadata_csv(url_to_meta, folder / "url_metadata.csv")
                print("✅  Generated URL metadata and exported url_metadata.csv")
            else:
                print("ℹ️  No external URLs found in the texts")
        except Exception as e:
            print(f"⚠️  Failed generating URL metadata: {e}")
        
        # Create combined lookup for export functions (likes take precedence over parents, parents over self)
        combined_lookup = self_tweet_lookup.copy()  # Start with self tweets as base
        combined_lookup.update(parent_lookup)       # Override with parents
        combined_lookup.update(like_lookup)         # Override with likes (likes take precedence)
        
        # Export text files with image captions and URL metadata replaced
        if tweets:
            export_tweets_text(tweets, combined_lookup, folder / "tweets_for_llm.txt", url_to_caption, url_to_meta, all_url_mappings)
            print("✅  Exported tweets_for_llm.txt")
        
        if like_lookup:
            export_likes_text(like_lookup, folder / "likes_for_llm.txt", url_to_caption, url_to_meta, all_url_mappings)
            print("✅  Exported likes_for_llm.txt")
        
        if bookmarks:
            export_bookmarks_text(bookmarks, folder / "bookmarks_for_llm.txt", url_to_caption, url_to_meta, all_url_mappings)
            print("✅  Exported bookmarks_for_llm.txt")
            
    except Exception as e:
        error_msg = f"Failed processing files: {e}"
        print(f"❌  {error_msg}")
        safe_messagebox("error", "Processing Error", error_msg)
        return

    success_msg = f"✅  Done! Output files written to:\n{folder}"
    print(f"\n{success_msg}")
    safe_messagebox("info", "Success", success_msg)


if __name__ == "__main__":  # pragma: no cover
    main()
