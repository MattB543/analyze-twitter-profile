# Twitter Profile Analyzer

Complete Twitter/X data extraction and analysis pipeline with Firefox extension and Python processor.

## Features

- **Firefox Extension**: Captures tweets, likes, bookmarks, and replies via GraphQL interception
- **Smart Detection**: Automatically detects timeline type and generates appropriate filenames
- **Parent Context**: Hydrates reply and quote tweet relationships via TwitterAPI.io (simpler than official Twitter API)
- **Data Cleaning**: Converts raw Twitter data to simplified, consistent format
- **Rich Processing**: Adds image captions (Gemini API) and URL metadata
- **LLM Ready**: Outputs clean text files optimized for language model analysis with conversation context

## Quick Start

### 1. Firefox Extension

1. Load extension in Firefox: `about:debugging` → "This Firefox" → "Load Temporary Add-on"
2. Select `firefox-scraper/manifest.json`
3. Navigate to your Twitter profile page
4. Click extension popup → Select data types → "Start capture"
5. Extension auto-scrolls and downloads JSONL files

### 2. Python Processing

```bash
# Install dependencies
pip install requests beautifulsoup4 google-genai python-dotenv

# Set up API keys in .env file or environment
export GEMINI_API_KEY="your-gemini-key"
export TWITTERAPI_KEY="pk_live_yourKeyHere"  # Get from https://twitterapi.io/

# Step 1: Clean data and hydrate parent tweets
python hydrate_parents_api.py

# Step 2: Process cleaned data into LLM format
python twitter-processer/twitter-processer.py
```

## Architecture

### Firefox Extension (MV2)

- **`interceptor.js`** - Content script that injects page patches and relays GraphQL responses to background
- **`page_patch.js`** - Main-world script that monkey-patches fetch/XMLHttpRequest to intercept GraphQL traffic
- **`background.js`** - Service worker that stores captured tweets, controls scrolling, and downloads JSONL files
- **`scroller.js`** - Auto-scroll implementation with idle detection and manual stop capability
- **`popup.html/js`** - Extension UI for manual control and configuration

### Python Pipeline

- **`hydrate_parents_api.py`** - Cleans raw data and fetches missing parent/quoted tweets via TwitterAPI.io
- **`twitter-processer.py`** - Processes cleaned data with image captioning, URL metadata, and context chains

## Data Flow

1. Extension intercepts GraphQL responses on Twitter/X pages for `UserTweets`, `LikesTimeline`, `TweetDetail`, and bookmark operations
2. `flatten()` function in `background.js` converts raw tweet objects to simplified format including `parent_ids`
3. Downloads as timestamped JSONL: `tweets_*.jsonl`, `likes_*.jsonl`, `bookmarks_*.jsonl`
4. `hydrate_parents_api.py` cleans raw data and fetches missing parent tweets via TwitterAPI.io (100k free credits ≈ 6,600 tweets)
5. `twitter-processer.py` processes cleaned data, generates image captions and URL metadata, builds conversation context
6. Outputs three LLM-ready text files: `tweets_for_llm.txt`, `likes_for_llm.txt`, `bookmarks_for_llm.txt`

## Requirements

- Firefox (MV2 extension support)
- Python 3.10+
- Gemini API key (for image captioning)
- TwitterAPI.io key (optional, for parent tweet hydration - much simpler than official Twitter API)

## License

MIT License - see LICENSE file for details.

## Development

Built with [Claude Code](https://claude.ai/code) - see `CLAUDE.md` for development guidance.
