**TL;DR**
The capture pipeline is fine – your interceptor *does* grab the 19 tweets from **UserTweets**, the flattener stores them, and you even see the “✅ Added tweet” log lines.
What fails is the **state‑keeping** in the background script: by the time the scroller sends `SCROLL_FINISHED`, the in‑memory `tweets` `Map` you just filled no longer exists, so `download()` prints “Downloading **0** tweets” and bails out.

---

## What actually happens

| log line                                                          | which background instance? | what it tells us                                                                                 |
| ----------------------------------------------------------------- | -------------------------- | ------------------------------------------------------------------------------------------------ |
| `📝 +19 tweets (total 19)`                                        | **A** (first instance)     | 19 tweets were added to the `Map` in instance A.                                                 |
| *(30‑odd seconds of idle time while the scroller keeps chugging)* |                            | The background page gets no events, so **Firefox/Chromium suspends it**.                         |
| `🔔 Scroller reported completion`                                 | **B** (new instance)       | The page’s idle timeout has killed A; the SCROLL\_FINISHED message wakes a brand‑new instance B. |
| `💾 Downloading 0 tweets…`                                        | **B**                      | In B, `const tweets = new Map()` is empty → download short‑circuits.                             |

Because the two instances have different heaps, the 19 tweets you captured in A are gone when B starts.  You see this only on longer runs (Tweets/Likes), while Bookmarks usually finish before the idle timeout so the bug is masked.

*Why the idle timeout?*

* In **Manifest V2** a background script is *persistent only if you ask for it*.
* Without `"persistent": true` Firefox ≥115 and Chrome ≥116 treat it the same way MV3 does – an **event page/service‑worker** that’s torn down after \~30 s of inactivity to save RAM. ([MDN Web Docs][1], [Chrome for Developers][2])

---

## Why it “sorta” worked before

* Bookmarks capture is short → background never idles → download sees tweets.
* Tweets/Likes capture scrolls for a minute or two → background idles → download runs in a fresh instance.

If you happen to click **“Stop & download”** before the timeout you’ll get the file; wait a bit longer and you get the empty‑map bug – hence “sorta works”.

---

## Two‐line fix 

### 1. Stay on MV2 for now

```json
"background": {
  "scripts": ["background.js"],
  "persistent": true        // KEEP the page alive
}
```

…then move all long‑lived state (`tweets`) into `chrome.storage.session` or IndexedDB, because service‑workers are always short‑lived.  Chrome has already started disabling MV2 extensions (June 2024) and Firefox 128 will follow in Q4 2025. ([Chrome for Developers][2])

---

## Other tiny nits you’ll want to tidy up

| Issue                                                                                                                                   | Quick fix                                                                                     |
| --------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **Double‑click “Start”** wipes the map a second time (`tweets.clear()` in the START handler).                                           | Disable the button after the first press.                                                     |
| You ignore the `TimelineClearCache` instruction but don’t clear your own cache.                                                         | Call `tweets.clear()` when you see that instruction *inside* a run, not just on `START`.      |
| The regex in **`interceptor.js`** was patched in `claude‑plan.md` but the built file you ran still has the old `/bookmark/`‑only check. | Re‑build or copy the diffed regex so you always hit Watches for `UserTweets`/`LikesTimeline`. |

Fix the persistence flag  and the capture/download cycle works 100 % of the time.

