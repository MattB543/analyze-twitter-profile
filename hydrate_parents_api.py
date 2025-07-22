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
import tkinter as tk
from tkinter import filedialog
from typing import Set, List, Dict, Any, Iterator
# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv is not installed; continue without loading .env
    pass

# Configuration
API_KEY = os.getenv("TWITTERAPI_KEY")
if not API_KEY:
    print("❌ Error: TWITTERAPI_KEY environment variable not set")
    print("   Get your key from https://twitterapi.io/ and set:")
    print("   export TWITTERAPI_KEY='pk_live_yourKeyHere'")
    sys.exit(1)

# File type processing configuration - set to False to skip processing that file type
PROCESS_TWEETS = True          # Process tweets_*.jsonl files
PROCESS_LIKES = True           # Process likes_*.jsonl files  
PROCESS_BOOKMARKS = True       # Process bookmarks_*.jsonl files
PROCESS_REPLIES = False         # Process replies_*.jsonl files

BATCH_SIZE = 100                               # IDs per request (safe ceiling)
BASE_URL = "https://api.twitterapi.io/twitter/tweets"
HEADERS = {"x-api-key": API_KEY}
CREDITS_PER_TWEET = 15                         # Cost per tweet
RATE_LIMIT_DELAY = 0.05                        # ~20 QPS to stay well under 200 QPS limit
MAX_RETRIES = 3                                # Maximum retry attempts for rate limits
RETRY_BACKOFF = [60, 120, 300]                 # Backoff delays in seconds (1min, 2min, 5min)

def clean_tweet(raw_tweet):
    """
    Takes a raw tweet object (as a dictionary) and returns a cleaned,
    simplified dictionary based on the specified requirements.
    """
    if not raw_tweet or not isinstance(raw_tweet, dict):
        return None  # Skip invalid tweets
    
    # Use .get() to safely access nested keys that might not exist
    legacy = raw_tweet.get('raw', {}).get('legacy', {})
    core_user_results = raw_tweet.get('raw', {}).get('core', {}).get('user_results', {}).get('result', {})
    core_user = core_user_results.get('core', {})
    
    # 1. Extract Basic Information
    cleaned_data = {
        'id': legacy.get('id_str') or raw_tweet.get('tweet_id'),
        'created_at': legacy.get('created_at') or raw_tweet.get('created_at'),
        'screen_name': core_user.get('screen_name', 'unknown_user'),
        # Add author_id
        'author_id': core_user_results.get('rest_id')
    }

    # 2. Extract Full Text (handles long tweets/notes)
    note_text = raw_tweet.get('raw', {}).get('note_tweet', {}).get('note_tweet_results', {}).get('result', {}).get('text')
    cleaned_data['text'] = note_text or legacy.get('full_text') or raw_tweet.get('text', '')

    # 3. Determine Interaction Type and Linked Data
    cleaned_data['interaction_type'] = 'tweet' # Default
    cleaned_data['linked_tweet_id'] = None
    # Add fields for replies and retweets
    cleaned_data['reply_to_screen_name'] = None
    cleaned_data['retweeted_text'] = None
    cleaned_data['retweeted_screen_name'] = None

    if legacy.get('in_reply_to_status_id_str'):
        cleaned_data['interaction_type'] = 'reply'
        cleaned_data['linked_tweet_id'] = legacy.get('in_reply_to_status_id_str')
        cleaned_data['reply_to_screen_name'] = legacy.get('in_reply_to_screen_name')
    elif legacy.get('quoted_status_id_str'):
        cleaned_data['interaction_type'] = 'quote_tweet'
        cleaned_data['linked_tweet_id'] = legacy.get('quoted_status_id_str')
    elif legacy.get('retweeted_status_result'):
        cleaned_data['interaction_type'] = 'retweet'
        retweeted_result = legacy.get('retweeted_status_result', {}).get('result', {})
        
        # The ID of the original tweet that was retweeted
        cleaned_data['linked_tweet_id'] = retweeted_result.get('rest_id')
        
        # Extract original tweet's text and author
        rt_legacy = retweeted_result.get('legacy', {})
        rt_note = retweeted_result.get('note_tweet', {}).get('note_tweet_results', {}).get('result', {})
        cleaned_data['retweeted_text'] = rt_note.get('text') or rt_legacy.get('full_text')
        
        # Extract retweeted user's screen name with multiple fallback paths
        retweeted_screen_name = None
        
        # Method 1: Core user results legacy path
        rt_user = retweeted_result.get('core', {}).get('user_results', {}).get('result', {}).get('legacy', {})
        if rt_user:
            retweeted_screen_name = rt_user.get('screen_name')
        
        # Method 2: Core user results core path (alternative structure)
        if not retweeted_screen_name:
            rt_user_core = retweeted_result.get('core', {}).get('user_results', {}).get('result', {}).get('core', {})
            if rt_user_core:
                retweeted_screen_name = rt_user_core.get('screen_name')
        
        # Method 3: Direct user object in retweeted result
        if not retweeted_screen_name:
            rt_user_direct = retweeted_result.get('user', {})
            if rt_user_direct:
                retweeted_screen_name = rt_user_direct.get('screen_name')
        
        cleaned_data['retweeted_screen_name'] = retweeted_screen_name

    if legacy.get('bookmarked'):
         if cleaned_data['interaction_type'] == 'tweet':
              cleaned_data['interaction_type'] = 'bookmark'

    # 4. Extract URLs
    urls = []
    for url_entity in legacy.get('entities', {}).get('urls', []):
        if 'expanded_url' in url_entity:
            urls.append(url_entity['expanded_url'])
            
    card = raw_tweet.get('raw', {}).get('card', {})
    if card:
        for item in card.get('legacy', {}).get('binding_values', []):
            if item.get('key') == 'card_url':
                urls.append(item.get('value', {}).get('string_value'))

    cleaned_data['urls'] = list(set(urls))

    # 5. Extract Media URLs
    media_urls = []
    extended_entities = legacy.get('extended_entities', {})
    if extended_entities and 'media' in extended_entities:
        for media_item in extended_entities['media']:
            media_urls.append(media_item.get('media_url_https'))
            
    cleaned_data['media_urls'] = media_urls

    return cleaned_data

def get_folder_path() -> pathlib.Path:
    """Get folder path via GUI folder picker."""
    try:
        root = tk.Tk()
        root.withdraw()  # Hide the main window
        
        folder_path = filedialog.askdirectory(
            title="Select folder containing Twitter export files"
        )
        
        if not folder_path:
            print("❌ No folder selected. Exiting.")
            sys.exit(1)
            
        folder = pathlib.Path(folder_path)
        print(f"📁 Selected folder: {folder}")
        return folder
        
    except Exception as e:
        print(f"⚠️ GUI not available: {e}")
        print("💡 Falling back to current directory")
        return pathlib.Path(".")

def find_and_clean_files(folder: pathlib.Path) -> List[pathlib.Path]:
    """
    Find input JSONL files in the folder and create cleaned versions.
    
    Args:
        folder: Path to folder containing input files
        
    Returns:
        List of paths to cleaned files
    """
    # Build list of JSONL patterns based on configuration
    jsonl_patterns = []
    if PROCESS_TWEETS:
        jsonl_patterns.append("tweets_*.jsonl")
    if PROCESS_LIKES:
        jsonl_patterns.append("likes_*.jsonl")
    if PROCESS_BOOKMARKS:
        jsonl_patterns.append("bookmarks_*.jsonl")
    if PROCESS_REPLIES:
        jsonl_patterns.append("replies_*.jsonl")
    
    if not jsonl_patterns:
        print("⚠️ No file types enabled for processing")
        return []
    
    input_files = []
    cleaned_files = []
    
    # Find input files
    for pattern in jsonl_patterns:
        for file_path in folder.glob(pattern):
            input_files.append(file_path)
    
    if not input_files:
        print(f"❌ No input files found in {folder}")
        print(f"   Looking for patterns: {', '.join(jsonl_patterns)}")
        return []
    
    print(f"📁 Found {len(input_files)} input files to clean")
    
    # Clean each file
    for input_file in input_files:
        # Generate cleaned filename (e.g., tweets_2025-01-01.jsonl -> cleaned_tweets_2025-01-01.jsonl)
        stem = input_file.stem  # e.g., "tweets_2025-01-01"
        cleaned_stem = f"cleaned_{stem}"
        cleaned_file = input_file.parent / f"{cleaned_stem}.jsonl"
        
        print(f"🧹 Cleaning {input_file.name} -> {cleaned_file.name}")
        
        try:
            with input_file.open('r', encoding='utf-8') as infile, cleaned_file.open('w', encoding='utf-8') as outfile:
                cleaned_count = 0
                error_count = 0
                
                for line_no, line in enumerate(infile, 1):
                    line = line.strip()
                    if not line:
                        continue
                        
                    try:
                        raw_tweet_data = json.loads(line)
                        cleaned_tweet = clean_tweet(raw_tweet_data)
                        if cleaned_tweet is not None:
                            outfile.write(json.dumps(cleaned_tweet) + '\n')
                            cleaned_count += 1
                        else:
                            print(f"⚠️ Skipping invalid tweet data on line {line_no} in {input_file.name}")
                            error_count += 1
                    except json.JSONDecodeError:
                        print(f"⚠️ Skipping invalid JSON line {line_no} in {input_file.name}")
                        error_count += 1
                        continue
                    except Exception as e:
                        print(f"⚠️ Error processing line {line_no} in {input_file.name}: {e}")
                        error_count += 1
                        continue
                
                print(f"✅ Cleaned {cleaned_count} tweets to {cleaned_file.name}")
                if error_count > 0:
                    print(f"⚠️ {error_count} errors encountered during cleaning")
                
                cleaned_files.append(cleaned_file)
                
        except Exception as e:
            print(f"❌ Failed to clean {input_file.name}: {e}")
            continue
    
    return cleaned_files

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
        
        # Check GraphQL quoted_status_result structure
        quoted_status_result = tweet.get("quoted_status_result", {})
        if quoted_status_result:
            quoted_result = quoted_status_result.get("result", {})
            if quoted_result:
                # Extract rest_id from the quoted tweet result
                quoted_rest_id = quoted_result.get("rest_id")
                if quoted_rest_id:
                    quoted_ids.add(str(quoted_rest_id))
        
        # Check for quotedRefResult (another GraphQL format)
        quoted_ref_result = tweet.get("quotedRefResult", {})
        if quoted_ref_result:
            result = quoted_ref_result.get("result", {})
            if result and result.get("__typename") == "Tweet":
                quoted_id = result.get("rest_id")
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
        
        # Retry loop for rate limits
        success = False
        for attempt in range(MAX_RETRIES):
            try:
                # Make API request with expansions to guarantee relationship fields
                params = {
                    "tweet_ids": ",".join(batch),
                    "expansions": "referenced_tweets.id,author_id"
                }
                response = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=30)
                
                if response.status_code == 429:
                    if attempt < MAX_RETRIES - 1:  # Not the last attempt
                        backoff_delay = RETRY_BACKOFF[attempt]
                        print(f"⚠️  Rate limited (attempt {attempt + 1}/{MAX_RETRIES}), waiting {backoff_delay} seconds...")
                        time.sleep(backoff_delay)
                        continue
                    else:
                        print(f"❌ Rate limited after {MAX_RETRIES} attempts, skipping batch")
                        break
                    
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
                
                success = True
                break  # Successfully processed, exit retry loop
                
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    print(f"❌ API request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                    time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                    continue
                else:
                    print(f"❌ API request failed after {MAX_RETRIES} attempts for batch {processed_batches}: {e}")
                    break
            except json.JSONDecodeError as e:
                if attempt < MAX_RETRIES - 1:
                    print(f"❌ JSON parse failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                    time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                    continue
                else:
                    print(f"❌ JSON parse failed after {MAX_RETRIES} attempts for batch {processed_batches}: {e}")
                    break
        else:
            # This executes if the retry loop completed without breaking (all retries exhausted)
            print(f"❌ All retry attempts exhausted for batch {processed_batches}")
            failed_ids.extend(batch)
            continue
        
        # If we didn't succeed, mark batch as failed
        if not success:
            failed_ids.extend(batch)
        
        # Rate limiting between successful batches
        if success and processed_batches < total_batches:
            time.sleep(RATE_LIMIT_DELAY)
    
    # Save failed IDs to file for audit
    if failed_ids:
        try:
            failed_path = pathlib.Path("failed_hydration_ids.txt")
            failed_path.write_text("\n".join(failed_ids), encoding="utf-8")
            print(f"📝 Saved {len(failed_ids)} failed tweet IDs to {failed_path}")
        except Exception as e:
            print(f"⚠️  Failed to save failed IDs: {e}")

def extract_parent_ids_from_cleaned_files(cleaned_files: List[pathlib.Path]) -> Set[str]:
    """
    Extract all parent tweet IDs from cleaned JSONL files.
    
    Args:
        cleaned_files: List of paths to cleaned JSONL files
        
    Returns:
        Set of unique parent tweet IDs
    """
    all_parent_ids = set()
    
    for file_path in cleaned_files:
        print(f"📁 Scanning {file_path.name} for parent IDs...")
        
        try:
            content = file_path.read_text(encoding="utf-8")
            for line_no, line in enumerate(content.splitlines(), 1):
                if not line.strip():
                    continue
                
                try:
                    obj = json.loads(line)
                    
                    # Extract parent IDs from cleaned format
                    linked_tweet_id = obj.get("linked_tweet_id")
                    if linked_tweet_id:
                        all_parent_ids.add(str(linked_tweet_id))
                        
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
    print("🧹 Now with file cleaning functionality!")
    print("=" * 60)
    
    # Step 0: Get folder and clean input files
    print("\n📁 Step 0: Selecting folder and cleaning input files...")
    folder = get_folder_path()
    cleaned_files = find_and_clean_files(folder)
    
    if not cleaned_files:
        print("❌ No files were successfully cleaned. Exiting.")
        return
    
    print(f"✅ Successfully cleaned {len(cleaned_files)} files")
    
    # Step 1: Extract parent IDs from cleaned JSONL files  
    print("\n📋 Step 1: Extracting parent tweet IDs from cleaned files...")
    all_parent_ids = extract_parent_ids_from_cleaned_files(cleaned_files)
    
    if not all_parent_ids:
        print("ℹ️  No parent tweet IDs found in cleaned files")
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
    
    if not ids_to_process:
        print("ℹ️  All parent tweets already exist in parents.json")
    else:
        print(f"🆕 Need to hydrate {len(ids_to_process)} new parent tweets")
    
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
    print(f"\n💾 Step 4: Saving results to {folder / 'parents.json'}...")
    all_tweets = {**existing_tweets, **all_new_tweets}
    
    if all_new_tweets:
        # Save parents.json to the selected folder
        parents_path = folder / "parents.json"
        try:
            with parents_path.open("w", encoding="utf-8") as f:
                json.dump(all_tweets, f, indent=2, ensure_ascii=False)
            print(f"💾 Saved {len(all_tweets)} tweets to {parents_path}")
        except Exception as e:
            print(f"❌ Failed to save {parents_path}: {e}")
        
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
    
    print("\n🎉 Cleaning and hydration complete!")
    print(f"📁 Output files saved to: {folder}")
    print(f"   - Cleaned files: {[f.name for f in cleaned_files]}")
    if all_new_tweets:
        print(f"   - Parent tweets: parents.json")

if __name__ == "__main__":
    main()