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
Python 3.9+ from the standard library only.
"""

import json
import re
import sys
import tkinter as tk
import pathlib
import csv
import requests
import mimetypes
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict, List
from google import genai
from google.genai import types
from bs4 import BeautifulSoup

client = genai.Client()                       # reads GEMINI_API_KEY

IMG_RE = re.compile(r"https://pbs.twimg.com/\S+\.(?:jpg|png|webp)")
# Regex for general external URLs (excluding Twitter image URLs)
URL_RE = re.compile(r"https?://(?!pbs\.twimg\.com)\S+")

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
    except Exception:  # fall back
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

def parse_likes_jsonl(likes_file: Path) -> Dict[str, str]:
    """Parse likes from JSONL file exported by Firefox extension."""
    likes_lookup = {}
    for line_no, line in enumerate(likes_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            tweet_id = obj.get("tweet_id", "")
            text = obj.get("text", "")
            if tweet_id and text:
                likes_lookup[tweet_id] = text
        except json.JSONDecodeError:
            print(f"⚠️  Skipping malformed JSON on line {line_no} in {likes_file.name}", file=sys.stderr)
            continue
    return likes_lookup


def load_parents_json(parents_file: Path) -> Dict[str, str]:
    """Load parent tweets from parents.json and convert to tweet_id -> text mapping."""
    try:
        with parents_file.open('r', encoding='utf-8') as f:
            parents_data = json.load(f)
        
        # Convert Twitter API v2 format to our lookup format
        parent_lookup = {}
        for tweet_id, tweet_data in parents_data.items():
            # Extract text from Twitter API v2 response format
            text = tweet_data.get('text', '')
            if text:
                parent_lookup[tweet_id] = text
        
        print(f"📖  Loaded {len(parent_lookup)} parent tweets from {parents_file.name}")
        return parent_lookup
        
    except Exception as e:
        print(f"⚠️  Failed to load parent tweets: {e}")
        return {}


def parse_tweets_jsonl(tweets_file: Path) -> List[Dict[str, Any]]:
    """Parse tweets from JSONL file exported by Firefox extension."""
    tweets = []
    for line_no, line in enumerate(tweets_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            
            # Extract parent IDs from the new format
            parent_ids = obj.get("parent_ids", [])
            reply_to_tweet_id = parent_ids[0] if parent_ids else ""
            quoted_tweet_id = parent_ids[1] if len(parent_ids) > 1 else ""
            
            tweets.append({
                "id": obj.get("tweet_id", ""),
                "created_at": obj.get("created_at", ""),
                "text": obj.get("text", ""),
                "is_retweet": obj.get("retweet", 0) > 0,
                "is_reply": obj.get("reply", 0) > 0,
                "quoted_tweet_id": quoted_tweet_id,
                "reply_to_tweet_id": reply_to_tweet_id,
                "reply_to_user": "",  # Not available in new format
            })
        except json.JSONDecodeError:
            print(f"⚠️  Skipping malformed JSON on line {line_no} in {tweets_file.name}", file=sys.stderr)
            continue
    return tweets


def export_tweets_text(tweets: List[Dict[str, Any]],
                       tweet_lookup: Dict[str, str],
                       outfile: Path,
                       url_to_caption: Dict[str, str] = None,
                       url_to_meta: Dict[str, str] = None) -> None:
    with outfile.open("w", encoding="utf-8") as f:
        last = len(tweets) - 1
        for i, tw in enumerate(tweets):
            # Add context for replies / quotes if we have it
            if tw["is_reply"] and tw["reply_to_tweet_id"]:
                original = tweet_lookup.get(tw["reply_to_tweet_id"], "")
                if original:
                    if url_to_caption:
                        original = replace_images_with_captions(original, url_to_caption)
                    if url_to_meta:
                        original = replace_urls_with_meta(original, url_to_meta)
                    f.write(f"Original (@{tw['reply_to_user']}):\n{original}\n\nMy Reply:\n")
            elif tw["quoted_tweet_id"]:
                original = tweet_lookup.get(tw["quoted_tweet_id"], "")
                if original:
                    if url_to_caption:
                        original = replace_images_with_captions(original, url_to_caption)
                    if url_to_meta:
                        original = replace_urls_with_meta(original, url_to_meta)
                    f.write(f"Quoted tweet:\n{original}\n\nQuote:\n")

            text = tw["text"].replace("\r", "")
            if url_to_caption:
                text = replace_images_with_captions(text, url_to_caption)
            if url_to_meta:
                text = replace_urls_with_meta(text, url_to_meta)
            f.write(text)
            if i != last:
                f.write("\n---\n")


def export_likes_text(tweet_lookup: Dict[str, str], outfile: Path, url_to_caption: Dict[str, str] = None, url_to_meta: Dict[str, str] = None) -> None:
    with outfile.open("w", encoding="utf-8") as f:
        ids = list(tweet_lookup)
        last = len(ids) - 1
        for i, tid in enumerate(ids):
            text = tweet_lookup[tid].replace("\r", "")
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

def parse_bookmarks_jsonl(bookmarks_file: Path) -> List[Dict[str, Any]]:
    """Parse bookmarks from JSONL file - handles both old and new formats."""
    tweets = []
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
            # New format - much simpler
            tweets.append({
                "screen_name": "",  # Not available in new format
                "full_text": obj.get("text", ""),
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
        url_mappings = {}
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
                                    # Extract URL mappings from legacy
                entities = legacy.get("entities", {})
                urls = entities.get("urls", [])
                for url_data in urls:
                    shortened = url_data.get("url", "")
                    expanded = url_data.get("expanded_url", "")
                    if shortened and expanded:
                        url_mappings[shortened] = expanded
                
                # Extract media URL mappings from legacy (prefer extended_entities if available)
                extended_entities = legacy.get("extended_entities", {})
                media_source = extended_entities.get("media", []) or entities.get("media", [])
                for media_data in media_source:
                    shortened = media_data.get("url", "")
                    media_url = media_data.get("media_url_https", "")
                    if shortened and media_url:
                        url_mappings[shortened] = media_url
        
        # If no text found in raw data, fall back to top-level text
        if not full_text:
            full_text = obj.get("text", "")
        
        # Replace shortened URLs with expanded URLs
        for shortened, expanded in url_mappings.items():
            full_text = full_text.replace(shortened, expanded)
        
        tweets.append({
            "screen_name": '@' + screen_name if screen_name else "",
            "full_text": full_text,
        })
    return tweets


def export_bookmarks_text(bookmarks: List[Dict[str, Any]], outfile: Path, url_to_caption: Dict[str, str] = None, url_to_meta: Dict[str, str] = None) -> None:
    with outfile.open("w", encoding="utf-8") as f:
        last = len(bookmarks) - 1
        for i, tw in enumerate(bookmarks):
            screen_name = tw.get("screen_name", "")
            full_text = tw.get("full_text", "").replace("\r", "")
            
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

def fetch_url_metadata(url: str) -> Dict[str, str]:
    """Fetch meta title and description from a URL.
    
    Returns:
        Dictionary with 'title' and 'description' keys
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
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
    except Exception as e:
        return {
            'title': f"ERROR: {e}",
            'description': ""
        }


def generate_url_metadata_from_texts(texts: List[str]) -> Dict[str, str]:
    """Generate metadata for all external URLs found in the given texts.
    
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
    
    # Generate metadata for each unique URL
    for url in all_urls:
        try:
            metadata = fetch_url_metadata(url)
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
            print(f"✅  Generated metadata for {url}")
        except Exception as e:
            url_to_meta[url] = url  # Keep original on error
            print(f"❌  Failed to get metadata for {url}: {e}")
    
    return url_to_meta


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

def describe_image(url: str, prompt="""You are an expert image analyst creating a summary for a language model that is analyzing social media posts. Your summary must be a single, dense paragraph.

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
    img_bytes = requests.get(url, timeout=15).content
    mime = mimetypes.guess_type(url)[0] or "image/jpeg"
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type=mime),
            prompt
        ],
    )
    return resp.text


def generate_image_captions_from_texts(texts: List[str]) -> Dict[str, str]:
    """Generate captions for all images found in the given texts.
    
    Returns:
        Dictionary mapping image URLs to their captions
    """
    url_to_caption = {}
    all_urls = set()
    
    # Collect all unique image URLs from all texts
    for text in texts:
        urls = IMG_RE.findall(text)
        all_urls.update(urls)
    
    # Generate captions for each unique URL
    for url in all_urls:
        try:
            caption = describe_image(url)
            url_to_caption[url] = caption
            print(f"✅  Generated caption for {url}")
        except Exception as e:
            caption = f"ERROR: {e}"
            url_to_caption[url] = caption
            print(f"❌  Failed to caption {url}: {e}")
    
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

def main() -> None:
    # Create a root window and hide it
    root = tk.Tk()
    root.withdraw()
    
    # Open folder picker dialog
    folder_path = filedialog.askdirectory(
        title="Select folder containing Twitter export files"
    )
    
    if not folder_path:
        print("❌  No folder selected. Exiting.")
        return
    
    folder = Path(folder_path)
    print(f"📁  Selected folder: {folder}")
    
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
        messagebox.showerror("Missing Files", error_msg)
        return
    
    # Process data and collect all texts for image analysis
    try:
        like_lookup = {}
        tweets = []
        
        if likes_file:
            print("\n🔄  Processing likes...")
            like_lookup = parse_likes_jsonl(likes_file)
        
        if tweets_file:
            print("🔄  Processing tweets...")
            tweets = parse_tweets_jsonl(tweets_file)
        
        # Load and merge parent tweets if available
        if parents_file:
            print("🔄  Loading parent tweets...")
            parent_lookup = load_parents_json(parents_file)
            # Merge parent tweets into like_lookup (our main tweet lookup)
            like_lookup.update(parent_lookup)
            print(f"📖  Total tweets in lookup: {len(like_lookup)} (including {len(parent_lookup)} parents)")
        
        all_texts = []
        
        # Collect tweet texts for image analysis
        for tw in tweets:
            all_texts.append(tw["text"])
            # Also include quoted/replied-to tweets if available
            if tw["reply_to_tweet_id"] and tw["reply_to_tweet_id"] in like_lookup:
                all_texts.append(like_lookup[tw["reply_to_tweet_id"]])
            if tw["quoted_tweet_id"] and tw["quoted_tweet_id"] in like_lookup:
                all_texts.append(like_lookup[tw["quoted_tweet_id"]])
        
        # Collect liked tweet texts
        all_texts.extend(like_lookup.values())
        
        # Process bookmarks if available
        bookmarks = None
        if bookmarks_file:
            print("🔄  Processing bookmarks...")
            bookmarks = parse_bookmarks_jsonl(bookmarks_file)
            # Add bookmark texts to analysis
            for bm in bookmarks:
                all_texts.append(bm.get("full_text", ""))
        
        # Generate image captions for all texts
        url_to_caption = {}
        try:
            print("🔄  Generating image captions...")
            url_to_caption = generate_image_captions_from_texts(all_texts)
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
        
        # Export text files with image captions and URL metadata replaced
        if tweets:
            export_tweets_text(tweets, like_lookup, folder / "tweets_for_llm.txt", url_to_caption, url_to_meta)
            print("✅  Exported tweets_for_llm.txt")
        
        if like_lookup:
            export_likes_text(like_lookup, folder / "likes_for_llm.txt", url_to_caption, url_to_meta)
            print("✅  Exported likes_for_llm.txt")
        
        if bookmarks:
            export_bookmarks_text(bookmarks, folder / "bookmarks_for_llm.txt", url_to_caption, url_to_meta)
            print("✅  Exported bookmarks_for_llm.txt")
            
    except Exception as e:
        error_msg = f"Failed processing files: {e}"
        print(f"❌  {error_msg}")
        messagebox.showerror("Processing Error", error_msg)
        return

    success_msg = f"✅  Done! Output files written to:\n{folder}"
    print(f"\n{success_msg}")
    messagebox.showinfo("Success", success_msg)


if __name__ == "__main__":  # pragma: no cover
    main()
