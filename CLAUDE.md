# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Twitter/X data extraction and analysis tool consisting of two main components:
1. **Firefox extension** (`firefox-scraper/`) - Extracts Twitter bookmarks, tweets, and likes via GraphQL interception
2. **Python processor** (`twitter-processer/`) - Converts extracted data to LLM-friendly format with image captions and URL metadata

## Architecture

### Firefox Extension (MV2)
- **`interceptor.js`** - Content script that injects page patches and relays GraphQL responses to background
- **`page_patch.js`** - Main-world script that monkey-patches fetch/XMLHttpRequest to intercept all GraphQL traffic  
- **`background.js`** - Service worker that stores captured tweets, controls scrolling, and downloads JSONL files
- **`scroller.js`** - Auto-scroll implementation with idle detection and manual stop capability
- **`popup.html/js`** - Extension UI for manual control

The extension now targets all Twitter/X pages and intercepts GraphQL responses for `UserTweets`, `LikesTimeline`, `TweetDetail`, and bookmark operations.

### Python Processor
- **`twitter-processer.py`** - Main script that processes exported Twitter data
- Reads `tweets_*.jsonl`, `likes_*.jsonl`, and `bookmarks_*.jsonl` files from the Firefox extension
- Uses Gemini API for image captioning (`GEMINI_API_KEY` required)
- Uses TwitterAPI.io for parent tweet hydration (`TWITTERAPI_KEY` required)
- Fetches URL metadata for external links
- Outputs three text files: `tweets_for_llm.txt`, `likes_for_llm.txt`, `bookmarks_for_llm.txt`

## Development Commands

### Firefox Extension
- Load extension in Firefox: `about:debugging` → "This Firefox" → "Load Temporary Add-on" → select `manifest.json`
- Test on: `https://x.com/i/bookmarks`

### Python Processing  
```bash
# Run the processor (opens folder picker dialog)
python twitter-processer/twitter-processer.py

# Hydrate parent tweets (optional, uses TwitterAPI.io)
python hydrate_parents_api.py

# Required environment variables
export GEMINI_API_KEY="your-api-key"
export TWITTERAPI_KEY="pk_live_yourKeyHere"  # Get from https://twitterapi.io/

# TwitterAPI.io provides 100k free credits ≈ 6,600 tweets (15 credits per tweet)
# Much simpler than official Twitter API - no OAuth, no rate limit issues
```

## Key Data Flow

1. Extension intercepts GraphQL responses on Twitter/X pages
2. `flatten()` function in `background.js:9` converts raw tweet objects to simplified format (now includes `parent_ids`)
3. Data is downloaded as JSONL files with timestamps: `bookmarks_YYYY-MM-DD-HH-MM-SS.jsonl`
4. `hydrate_parents_api.py` extracts missing parent tweet IDs and fetches them via TwitterAPI.io
5. Python processor loads all data sources including hydrated parents, generates image captions and URL metadata
6. Outputs plain text files suitable for LLM consumption

## Extension Configuration

The extension is configured via `manifest.json` to:
- Activate on all Twitter/X pages
- Inject scripts at `document_start` and `document_idle`
- Require download and storage permissions

## Implementation Status

✅ **Completed enhancements from claude-plan.md + claude-fix.md:**
1. ✅ Expanded GraphQL interception to capture `UserTweets`, `LikesTimeline`, `TweetDetail`  
2. ✅ Added `parent_ids` field to track tweet relationships
3. ✅ Created `hydrate_parents_api.py` for parent tweet hydration via TwitterAPI.io (replaces twarc2)
4. ✅ Modified processor to merge hydrated parent tweets into the main lookup
5. ✅ **Fixed critical wiring issues:**
   - Updated message protocol from `BOOKMARK_RESPONSE` to `TIMELINE_RESPONSE`
   - Added generic `extractTimeline()` function for all response types  
   - Removed forced navigation to `/i/bookmarks` (user can stay on any timeline)
   - Dynamic filenames: `tweets_*.jsonl`, `likes_*.jsonl`, `bookmarks_*.jsonl`
   - Updated manifest to "Twitter Exporter v0.3.0"
6. ✅ **Final touch-ups for production:**
   - Enhanced parent ID capture: includes `in_reply_to_status_id_str` and `quoted_status_id_str`
   - Removed `bookmark_count` field (doesn't exist in UserTweets/LikesTimeline responses)
   - Hardened filename scope detection for URLs with trailing slashes and query params

The extension now properly captures tweets, likes, and bookmarks from any Twitter timeline with full context via parent tweet hydration.