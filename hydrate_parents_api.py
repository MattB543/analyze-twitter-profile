#!/usr/bin/env python3
"""
hydrate_parents_api.py

Hydrate parent tweets using TwitterAPI.io instead of the official Twitter API.
This replaces the twarc2-based hydrate_parents.py with a simpler implementation
that uses TwitterAPI.io's REST endpoints.

Usage:
    export TWITTERAPI_KEY="pk_live_yourKeyHere"
    python hydrate_parents_api.py

Requirements:
    - TWITTERAPI_KEY environment variable
    - requests library
    - Input JSONL files in current directory (tweets_*.jsonl, likes_*.jsonl, bookmarks_*.jsonl)
    - Writes to parents.json (not JSONL for compatibility with existing processor)
"""

import os
import sys
import json
import time
import itertools
import requests
import pathlib
from typing import Set, List, Dict, Any, Iterator

# Configuration
API_KEY = os.getenv("TWITTERAPI_KEY")
if not API_KEY:
    print("❌ Error: TWITTERAPI_KEY environment variable not set")
    print("   Get your key from https://twitterapi.io/ and set:")
    print("   export TWITTERAPI_KEY='pk_live_yourKeyHere'")
    sys.exit(1)

BATCH_SIZE = 100                               # IDs per request (safe ceiling)
BASE_URL = "https://api.twitterapi.io/twitter/tweets"
HEADERS = {"x-api-key": API_KEY}
CREDITS_PER_TWEET = 15                         # Cost per tweet
RATE_LIMIT_DELAY = 0.05                        # ~20 QPS to stay well under 200 QPS limit

def chunks(lst: List[str], n: int) -> Iterator[List[str]]:
    """Yield successive n-sized chunks from lst."""
    it = iter(lst)
    while True:
        batch = list(itertools.islice(it, n))
        if not batch:
            break
        yield batch

def hydrate_tweets(tweet_ids: List[str]) -> Iterator[Dict[str, Any]]:
    """
    Hydrate tweets using TwitterAPI.io batch endpoint.
    
    Args:
        tweet_ids: List of tweet ID strings to hydrate
        
    Yields:
        Tweet objects from the API
    """
    if not tweet_ids:
        return
    
    total_batches = (len(tweet_ids) + BATCH_SIZE - 1) // BATCH_SIZE
    processed_batches = 0
    failed_ids = []
    
    for batch in chunks(tweet_ids, BATCH_SIZE):
        processed_batches += 1
        batch_size = len(batch)
        
        print(f"🔄 Hydrating batch {processed_batches}/{total_batches} ({batch_size} tweets)...")
        
        try:
            # Make API request
            params = {"tweet_ids": ",".join(batch)}
            response = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=30)
            
            if response.status_code == 429:
                print("⚠️  Rate limited, waiting 60 seconds...")
                time.sleep(60)
                continue
                
            response.raise_for_status()
            data = response.json()
            
            # Process results
            tweets = data.get("tweets", [])
            found_count = len(tweets)
            estimated_credits = max(found_count * CREDITS_PER_TWEET, 15)  # Minimum 15 credits per request
            
            # Track failed IDs (requested but not returned)
            found_ids = {tweet.get("id") or tweet.get("id_str") for tweet in tweets if tweet.get("id") or tweet.get("id_str")}
            batch_failed = [tid for tid in batch if tid not in found_ids]
            failed_ids.extend(batch_failed)
            
            print(f"✅ Found {found_count}/{batch_size} tweets (≈{estimated_credits} credits)")
            if batch_failed:
                print(f"⚠️  {len(batch_failed)} tweets not found in this batch")
            
            for tweet in tweets:
                yield tweet
                
        except requests.exceptions.RequestException as e:
            print(f"❌ API request failed for batch {processed_batches}: {e}")
            failed_ids.extend(batch)  # Mark entire batch as failed
            continue
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse JSON response for batch {processed_batches}: {e}")
            failed_ids.extend(batch)  # Mark entire batch as failed
            continue
        
        # Rate limiting
        if processed_batches < total_batches:
            time.sleep(RATE_LIMIT_DELAY)
    
    # Save failed IDs to file for audit
    if failed_ids:
        try:
            failed_path = pathlib.Path("failed_hydration_ids.txt")
            failed_path.write_text("\n".join(failed_ids), encoding="utf-8")
            print(f"📝 Saved {len(failed_ids)} failed tweet IDs to {failed_path}")
        except Exception as e:
            print(f"⚠️  Failed to save failed IDs: {e}")

def extract_parent_ids_from_jsonl_files() -> Set[str]:
    """
    Extract all parent tweet IDs from JSONL files in the current directory.
    
    Returns:
        Set of unique parent tweet IDs
    """
    all_parent_ids = set()
    
    # Look for tweets, likes, and bookmarks JSONL files
    jsonl_patterns = ["tweets_*.jsonl", "likes_*.jsonl", "bookmarks_*.jsonl"]
    
    for pattern in jsonl_patterns:
        for file_path in pathlib.Path(".").glob(pattern):
            print(f"📁 Scanning {file_path.name} for parent IDs...")
            
            try:
                content = file_path.read_text(encoding="utf-8")
                for line_no, line in enumerate(content.splitlines(), 1):
                    if not line.strip():
                        continue
                    
                    try:
                        obj = json.loads(line)
                        parent_ids = obj.get("parent_ids", [])
                        if parent_ids:
                            all_parent_ids.update(str(pid) for pid in parent_ids if pid)
                    except json.JSONDecodeError:
                        print(f"⚠️  Skipping malformed JSON on line {line_no} in {file_path.name}")
                        continue
                        
            except Exception as e:
                print(f"❌ Failed to read {file_path.name}: {e}")
                continue
    
    return all_parent_ids

def load_existing_parents() -> Dict[str, Dict[str, Any]]:
    """
    Load existing parent tweets from parents.json if it exists.
    
    Returns:
        Dictionary mapping tweet IDs to tweet data
    """
    parents_file = pathlib.Path("parents.json")
    if not parents_file.exists():
        return {}
    
    try:
        with parents_file.open("r", encoding="utf-8") as f:
            existing = json.load(f)
        print(f"📖 Loaded {len(existing)} existing parent tweets from parents.json")
        return existing
    except Exception as e:
        print(f"⚠️  Failed to load existing parents.json: {e}")
        return {}

def save_parents(parents: Dict[str, Dict[str, Any]]) -> None:
    """
    Save parent tweets to parents.json.
    
    Args:
        parents: Dictionary mapping tweet IDs to tweet data
    """
    try:
        with open("parents.json", "w", encoding="utf-8") as f:
            json.dump(parents, f, indent=2, ensure_ascii=False)
        print(f"💾 Saved {len(parents)} parent tweets to parents.json")
    except Exception as e:
        print(f"❌ Failed to save parents.json: {e}")

def main():
    print("🚀 TwitterAPI.io Parent Tweet Hydrator")
    print("=" * 50)
    
    # Step 1: Extract parent IDs from JSONL files
    print("\n📋 Step 1: Extracting parent tweet IDs...")
    all_parent_ids = extract_parent_ids_from_jsonl_files()
    
    if not all_parent_ids:
        print("ℹ️  No parent tweet IDs found in JSONL files")
        return
    
    print(f"📊 Found {len(all_parent_ids)} unique parent tweet IDs")
    
    # Step 2: Load existing parents to avoid re-hydrating
    print("\n📖 Step 2: Loading existing parent tweets...")
    existing_parents = load_existing_parents()
    
    # Step 3: Determine which IDs need hydration
    print("\n🔍 Step 3: Determining tweets to hydrate...")
    ids_to_hydrate = [tid for tid in all_parent_ids if tid not in existing_parents]
    
    if not ids_to_hydrate:
        print("✅ All parent tweets already hydrated!")
        return
    
    print(f"🎯 Need to hydrate {len(ids_to_hydrate)} new parent tweets")
    estimated_credits = len(ids_to_hydrate) * CREDITS_PER_TWEET
    print(f"💰 Estimated cost: {estimated_credits:,} credits")
    
    # Step 4: Hydrate missing tweets
    print(f"\n🔄 Step 4: Hydrating {len(ids_to_hydrate)} parent tweets...")
    new_parents = {}
    
    for tweet in hydrate_tweets(ids_to_hydrate):
        tweet_id = tweet.get("id") or tweet.get("id_str")
        if tweet_id:
            new_parents[str(tweet_id)] = tweet
    
    # Step 5: Merge and save results
    print(f"\n💾 Step 5: Saving results...")
    all_parents = {**existing_parents, **new_parents}
    
    if new_parents:
        save_parents(all_parents)
        print(f"✅ Successfully hydrated {len(new_parents)} new parent tweets")
        print(f"📊 Total parent tweets: {len(all_parents)}")
    else:
        print("⚠️  No new tweets were successfully hydrated")
    
    print("\n🎉 Hydration complete!")

if __name__ == "__main__":
    main()