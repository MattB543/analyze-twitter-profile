#!/usr/bin/env python3
"""
hydrate_parents_api.py

Hydrate parent tweets using TwitterAPI.io, including quoted tweets.
Now recursively fetches both parent tweets AND any tweets quoted by those parents.

Usage:
    export TWITTERAPI_KEY="pk_live_yourKeyHere"
    python hydrate_parents_api.py

Requirements:
    - TWITTERAPI_KEY environment variable
    - requests library
    - Input JSONL files in current directory (tweets_*.jsonl, likes_*.jsonl, bookmarks_*.jsonl)
    - Writes to parents.json (includes both parents and quoted tweets)
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

def extract_quoted_tweet_ids(tweets: List[Dict[str, Any]]) -> Set[str]:
    """
    Extract quoted tweet IDs from a list of tweets.
    
    Args:
        tweets: List of tweet objects
        
    Returns:
        Set of quoted tweet IDs
    """
    quoted_ids = set()
    
    for tweet in tweets:
        # Check referenced_tweets for quoted tweets (Twitter API v2 format)
        referenced = tweet.get("referenced_tweets", [])
        for ref in referenced:
            if ref.get("type") == "quoted" and ref.get("id"):
                quoted_ids.add(str(ref["id"]))
        
        # Also check legacy format (if present)
        legacy = tweet.get("legacy", {})
        quoted_id = legacy.get("quoted_status_id_str")
        if quoted_id:
            quoted_ids.add(str(quoted_id))
    
    return quoted_ids

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
            found_ids = {str(tweet.get("id") or tweet.get("id_str")) for tweet in tweets if tweet.get("id") or tweet.get("id_str")}
            batch_failed = [tid for tid in batch if tid not in found_ids]
            failed_ids.extend(batch_failed)
            
            print(f"✅ Found {found_count}/{batch_size} tweets (≈{estimated_credits} credits)")
            if batch_failed:
                print(f"⚠️  {len(batch_failed)} tweets not found in this batch")
            
            for tweet in tweets:
                yield tweet
                
        except requests.exceptions.RequestException as e:
            print(f"❌ API request failed for batch {processed_batches}: {e}")
            failed_ids.extend(batch)
            continue
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse JSON response for batch {processed_batches}: {e}")
            failed_ids.extend(batch)
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
        print(f"📖 Loaded {len(existing)} existing tweets from parents.json")
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
        print(f"💾 Saved {len(parents)} tweets to parents.json")
    except Exception as e:
        print(f"❌ Failed to save parents.json: {e}")

def main():
    print("🚀 TwitterAPI.io Parent & Quoted Tweet Hydrator")
    print("=" * 50)
    
    # Step 1: Extract parent IDs from JSONL files
    print("\n📋 Step 1: Extracting parent tweet IDs...")
    all_parent_ids = extract_parent_ids_from_jsonl_files()
    
    if not all_parent_ids:
        print("ℹ️  No parent tweet IDs found in JSONL files")
        return
    
    print(f"📊 Found {len(all_parent_ids)} unique parent tweet IDs")
    
    # Step 2: Load existing tweets to avoid re-hydrating
    print("\n📖 Step 2: Loading existing tweets...")
    existing_tweets = load_existing_parents()
    
    # Step 3: Hydrate tweets recursively (parents + quoted tweets)
    print("\n🔄 Step 3: Hydrating tweets with quoted tweet detection...")
    
    # Start with parent IDs that aren't already hydrated
    ids_to_process = [tid for tid in all_parent_ids if tid not in existing_tweets]
    all_new_tweets = {}
    max_depth = 3  # Limit recursion depth
    current_depth = 0
    
    while ids_to_process and current_depth < max_depth:
        current_depth += 1
        print(f"\n🔍 Depth {current_depth}: Processing {len(ids_to_process)} tweet IDs...")
        
        if current_depth == 1:
            print("   (Direct parent tweets)")
        else:
            print("   (Quoted tweets from previous level)")
        
        # Hydrate current batch
        new_tweets = []
        for tweet in hydrate_tweets(ids_to_process):
            tweet_id = str(tweet.get("id") or tweet.get("id_str"))
            if tweet_id:
                all_new_tweets[tweet_id] = tweet
                new_tweets.append(tweet)
        
        print(f"✅ Hydrated {len(new_tweets)} tweets at depth {current_depth}")
        
        # Extract quoted tweet IDs from newly hydrated tweets
        quoted_ids = extract_quoted_tweet_ids(new_tweets)
        
        # Filter out already hydrated quoted tweets
        quoted_to_fetch = [
            qid for qid in quoted_ids 
            if qid not in existing_tweets and qid not in all_new_tweets
        ]
        
        if quoted_to_fetch:
            print(f"🔗 Found {len(quoted_to_fetch)} quoted tweets to fetch next")
        
        # Prepare for next iteration
        ids_to_process = quoted_to_fetch
    
    # Step 4: Merge and save results
    print(f"\n💾 Step 4: Saving results...")
    all_tweets = {**existing_tweets, **all_new_tweets}
    
    if all_new_tweets:
        save_parents(all_tweets)
        print(f"✅ Successfully hydrated {len(all_new_tweets)} new tweets")
        print(f"📊 Total tweets in parents.json: {len(all_tweets)}")
        
        # Show breakdown
        parent_count = len([tid for tid in all_new_tweets if tid in all_parent_ids])
        quoted_count = len(all_new_tweets) - parent_count
        if quoted_count > 0:
            print(f"   - Direct parents: {parent_count}")
            print(f"   - Quoted tweets: {quoted_count}")
    else:
        print("⚠️  No new tweets were successfully hydrated")
    
    print("\n🎉 Hydration complete!")

if __name__ == "__main__":
    main()