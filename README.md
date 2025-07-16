# Twitter Profile Analyzer

Complete Twitter/X data extraction and analysis pipeline with Firefox extension and Python processor.

## Features

- **Firefox Extension**: Captures tweets, likes, and bookmarks via GraphQL interception
- **Smart Detection**: Automatically detects timeline type and generates appropriate filenames
- **Parent Context**: Hydrates reply and quote tweet relationships via Twitter API v2
- **Rich Processing**: Adds image captions (Gemini API) and URL metadata
- **LLM Ready**: Outputs clean text files optimized for language model analysis

## Quick Start

### 1. Firefox Extension

1. Load extension in Firefox: `about:debugging` → "This Firefox" → "Load Temporary Add-on"
2. Select `firefox-scraper/manifest.json`
3. Navigate to any Twitter timeline (tweets, likes, bookmarks)
4. Click extension popup → "Start capture"
5. Extension auto-scrolls and downloads JSONL file

### 2. Python Processing

```bash
# Install dependencies
pip install twarc requests beautifulsoup4 google-generativeai

# Set up API keys
export GEMINI_API_KEY="your-gemini-key"

# Configure Twitter API (optional, for parent tweet hydration)
pip install twarc
twarc2 configure

# Process extracted data
python twitter-processer/twitter-processer.py

# Hydrate parent tweets (optional)
python hydrate_parents.py
```

## Architecture

### Firefox Extension (MV2)
- **`page_patch.js`** - Intercepts GraphQL requests for `UserTweets`, `LikesTimeline`, `TweetDetail`, `bookmark`
- **`background.js`** - Processes timeline data, manages downloads with smart filename detection
- **`scroller.js`** - Auto-scroll with idle detection and manual controls

### Python Pipeline
- **`twitter-processer.py`** - Main processor with image captioning and URL metadata
- **`hydrate_parents.py`** - Fetches missing parent/quoted tweets via Twitter API v2

## Data Flow

1. Extension intercepts GraphQL responses on Twitter/X pages
2. `flatten()` function extracts tweet data including `parent_ids`
3. Downloads as timestamped JSONL: `tweets_*.jsonl`, `likes_*.jsonl`, `bookmarks_*.jsonl`
4. `hydrate_parents.py` fetches missing parent tweets via Twitter API v2
5. Python processor merges all data, adds image captions and URL metadata
6. Outputs LLM-ready text files

## Requirements

- Firefox (MV2 extension support)
- Python 3.9+
- Gemini API key (for image captioning)
- Twitter API v2 access (optional, for parent tweet hydration)

## License

MIT License - see LICENSE file for details.

## Development

Built with [Claude Code](https://claude.ai/code) - see `CLAUDE.md` for development guidance.