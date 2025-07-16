#!/usr/bin/env python3
"""hydrate_parents.py

Reads all JSONL files in a folder, extracts missing parent tweet IDs,
and hydrates them using the Twitter API v2 via twarc2.

Usage:
    python hydrate_parents.py [folder_path]

If no folder_path is provided, opens a folder picker dialog.
Requires twarc2 to be installed and configured with credentials.

Dependencies:
    pip install twarc
"""

import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Dict, List, Set

try:
    from twarc import Twarc2
except ImportError:
    print("❌ twarc not installed. Run: pip install twarc")
    sys.exit(1)


def chunks(lst: List, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def extract_parent_ids_from_jsonl(jsonl_path: Path) -> Set[str]:
    """Extract all parent_ids from a JSONL file."""
    parent_ids = set()
    
    try:
        with jsonl_path.open('r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                    
                try:
                    tweet_data = json.loads(line)
                    # Extract parent_ids if they exist
                    if 'parent_ids' in tweet_data:
                        for parent_id in tweet_data['parent_ids']:
                            if parent_id:  # Skip empty strings
                                parent_ids.add(parent_id)
                except json.JSONDecodeError:
                    print(f"⚠️  Skipping malformed JSON in {jsonl_path.name} line {line_no}")
                    continue
                    
    except Exception as e:
        print(f"❌ Error reading {jsonl_path}: {e}")
        
    return parent_ids


def extract_seen_tweet_ids_from_jsonl(jsonl_path: Path) -> Set[str]:
    """Extract all tweet_ids that we already have from a JSONL file."""
    seen_ids = set()
    
    try:
        with jsonl_path.open('r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                    
                try:
                    tweet_data = json.loads(line)
                    # Extract tweet_id if it exists
                    if 'tweet_id' in tweet_data:
                        tweet_id = tweet_data['tweet_id']
                        if tweet_id:
                            seen_ids.add(tweet_id)
                except json.JSONDecodeError:
                    print(f"⚠️  Skipping malformed JSON in {jsonl_path.name} line {line_no}")
                    continue
                    
    except Exception as e:
        print(f"❌ Error reading {jsonl_path}: {e}")
        
    return seen_ids


def hydrate_tweets(tweet_ids: List[str]) -> Dict:
    """Hydrate tweets using twarc2."""
    t = Twarc2()
    hydrated_tweets = {}
    
    print(f"🔄 Hydrating {len(tweet_ids)} parent tweets...")
    
    for chunk in chunks(tweet_ids, 100):  # API allows up to 100 IDs per request
        try:
            print(f"  📡 Fetching chunk of {len(chunk)} tweets...")
            for page in t.tweet_lookup(chunk):
                if 'data' in page:
                    for tweet in page['data']:
                        tweet_id = tweet.get('id')
                        if tweet_id:
                            hydrated_tweets[tweet_id] = tweet
                            
        except Exception as e:
            print(f"⚠️  Error hydrating chunk: {e}")
            continue
    
    print(f"✅ Successfully hydrated {len(hydrated_tweets)} tweets")
    return hydrated_tweets


def main():
    # Handle command line argument or use folder picker
    if len(sys.argv) > 1:
        folder_path = Path(sys.argv[1])
        if not folder_path.exists() or not folder_path.is_dir():
            print(f"❌ Invalid folder path: {folder_path}")
            sys.exit(1)
    else:
        # Create a root window and hide it
        root = tk.Tk()
        root.withdraw()
        
        # Open folder picker dialog
        folder_path = filedialog.askdirectory(
            title="Select folder containing JSONL files"
        )
        
        if not folder_path:
            print("❌ No folder selected. Exiting.")
            return
            
        folder_path = Path(folder_path)
    
    print(f"📁 Processing folder: {folder_path}")
    
    # Find all JSONL files
    jsonl_files = list(folder_path.glob("*.jsonl"))
    if not jsonl_files:
        print("❌ No JSONL files found in the selected folder")
        return
    
    print(f"📄 Found {len(jsonl_files)} JSONL files")
    
    # Extract all parent IDs and seen tweet IDs
    all_parent_ids = set()
    all_seen_ids = set()
    
    for jsonl_file in jsonl_files:
        print(f"🔍 Processing {jsonl_file.name}...")
        parent_ids = extract_parent_ids_from_jsonl(jsonl_file)
        seen_ids = extract_seen_tweet_ids_from_jsonl(jsonl_file)
        
        all_parent_ids.update(parent_ids)
        all_seen_ids.update(seen_ids)
        
        print(f"  Found {len(parent_ids)} parent IDs, {len(seen_ids)} seen tweet IDs")
    
    # Find missing parent IDs (those we need but don't already have)
    missing_ids = all_parent_ids - all_seen_ids
    
    print(f"📊 Summary:")
    print(f"  Total parent IDs found: {len(all_parent_ids)}")
    print(f"  Already have: {len(all_parent_ids & all_seen_ids)}")
    print(f"  Missing (need to hydrate): {len(missing_ids)}")
    
    if not missing_ids:
        print("✅ No missing parent tweets to hydrate!")
        return
    
    # Check rate limit estimate
    requests_needed = (len(missing_ids) + 99) // 100  # Round up division
    if requests_needed > 75:
        print(f"⚠️  Warning: Will need {requests_needed} requests, but rate limit is 75/15min")
        print("   Consider running in batches or waiting between runs")
    
    # Hydrate missing tweets
    try:
        hydrated_tweets = hydrate_tweets(list(missing_ids))
        
        # Save results
        output_path = folder_path / "parents.json"
        with output_path.open('w', encoding='utf-8') as f:
            json.dump(hydrated_tweets, f, indent=2, ensure_ascii=False)
        
        print(f"💾 Saved {len(hydrated_tweets)} hydrated tweets to {output_path}")
        
    except Exception as e:
        print(f"❌ Error during hydration: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()