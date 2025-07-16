/* global chrome */

// Cross-browser compatibility: alias chrome to browser
if (typeof browser === "undefined") {
  var browser = chrome;
}

// ───────────────────────────── helpers ─────────────────────────────
function flatten(tweet) {
  const l = tweet.legacy ?? {};
  const u = tweet?.core?.user_results?.result?.legacy ?? {};
  return {
    tweet_id: tweet.rest_id,
    created_at: l.created_at,
    text: l.full_text || l.text,
    lang: l.lang,
    favorite: l.favorite_count,
    retweet: l.retweet_count,
    user_handle: u.screen_name,
    parent_ids: [
      ...(l.referenced_tweets ?? []).map(r => r.id_str),
      l.in_reply_to_status_id_str,
      l.quoted_status_id_str,
    ].filter(Boolean),
    raw: tweet,
  };
}

function jsonl(map) {
  return [...map.values()].map((j) => JSON.stringify(j)).join("\n");
}

function extractTimeline(obj) {
  /**
   * Recursively search for any object containing `.instructions` and return it.
   * This works for:
   * - UserTweets: page.data.user.result.timeline.timeline
   * - LikesTimeline: page.data.user.result.timeline.timeline  
   * - TweetDetail: page.data.threaded_conversation_with_injections_v2
   * - bookmark: page.data.bookmark_timeline_v2.timeline
   */
  if (!obj || typeof obj !== 'object') return null;
  
  // Check if this object has instructions
  if (obj.instructions && Array.isArray(obj.instructions)) {
    return obj;
  }
  
  // Recursively search in all properties
  for (const value of Object.values(obj)) {
    const result = extractTimeline(value);
    if (result) return result;
  }
  
  return null;
}

// ─────────────────────── capture & download ───────────────────────
const tweets = new Map(); // tweet_id → flattened

async function blobToDataURL(blob) {
  return new Promise((res, rej) => {
    const reader = new FileReader();
    reader.onerror = () => rej(reader.error);
    reader.onloadend = () => res(reader.result); // result is data: URL
    reader.readAsDataURL(blob);
  });
}

async function download() {
  console.log(`💾 Downloading ${tweets.size} tweets…`);
  if (tweets.size === 0) {
    console.warn("⚠️  No tweets captured!");
    return;
  }

  // Determine filename based on current page
  let scope = "bookmarks"; // default
  try {
    const [tab] = await browser.tabs.query({
      active: true,
      currentWindow: true,
    });
    if (tab?.url) {
      if (/\/likes(?:\/|$|\?)/i.test(tab.url)) {
        scope = "likes";
      } else if (/\/i\/bookmarks/i.test(tab.url)) {
        scope = "bookmarks";
      } else if (/^https:\/\/(twitter|x)\.com\/[^\/?#]+(?:\/?$|\?)/i.test(tab.url)) {
        scope = "tweets";
      }
    }
  } catch (e) {
    console.warn("Could not determine page type, using 'bookmarks'", e);
  }

  try {
    const jsonData = jsonl(tweets);
    console.log(
      `📊 Data size: ${(jsonData.length / 1024 / 1024).toFixed(2)} MB`
    );

    const blob = new Blob([jsonData], {
      type: "application/x-jsonlines;charset=utf-8",
    });
    console.log(`📦 Blob size: ${(blob.size / 1024 / 1024).toFixed(2)} MB`);

    let url;
    let needsRevoke = false;
    let downloadMethod;

    if (typeof URL.createObjectURL === "function") {
      // Available in window pages, not in service-workers ➜ try first
      url = URL.createObjectURL(blob);
      needsRevoke = true;
      downloadMethod = "createObjectURL";
    } else {
      // Fallback: convert blob → data URL (safe for < ~50 MB)
      console.log("⚠️  Using data URL fallback (size limited to ~50MB)");
      url = await blobToDataURL(blob);
      downloadMethod = "dataURL";
    }

    console.log(`🔗 Download method: ${downloadMethod}`);
    console.log(
      `🔗 URL type: ${
        url.startsWith("blob:")
          ? "blob"
          : url.startsWith("data:")
          ? "data"
          : "unknown"
      }`
    );

    const ts = new Date().toISOString().replace(/[:T]/g, "-").split(".")[0];
    const filename = `${scope}_${ts}.jsonl`;

    console.log(`📁 Attempting download: ${filename}`);

    const downloadId = await browser.downloads.download({
      url,
      filename,
      conflictAction: "uniquify",
    });

    console.log(`✅ Download initiated with ID: ${downloadId}`);

    // Listen for download events
    const onChanged = (downloadDelta) => {
      if (downloadDelta.id === downloadId) {
        console.log(`📥 Download ${downloadId} state:`, downloadDelta);
        if (downloadDelta.state) {
          if (downloadDelta.state.current === "complete") {
            console.log(`🎉 Download ${downloadId} completed successfully!`);
            browser.downloads.onChanged.removeListener(onChanged);
          } else if (downloadDelta.state.current === "interrupted") {
            console.error(
              `❌ Download ${downloadId} was interrupted:`,
              downloadDelta.error
            );
            browser.downloads.onChanged.removeListener(onChanged);
          }
        }
        if (downloadDelta.error) {
          console.error(
            `❌ Download ${downloadId} error:`,
            downloadDelta.error
          );
        }
      }
    };

    browser.downloads.onChanged.addListener(onChanged);

    // Also query download status after a short delay
    setTimeout(async () => {
      try {
        const downloads = await browser.downloads.search({ id: downloadId });
        if (downloads.length > 0) {
          const download = downloads[0];
          console.log(`🔍 Download ${downloadId} status check:`, {
            state: download.state,
            error: download.error,
            filename: download.filename,
            totalBytes: download.totalBytes,
            bytesReceived: download.bytesReceived,
          });
        }
      } catch (e) {
        console.error("Error checking download status:", e);
      }
    }, 1000);

    if (needsRevoke) {
      // Delay revocation to ensure download has started
      setTimeout(() => {
        URL.revokeObjectURL(url);
        console.log("🧹 Blob URL revoked");
      }, 2000);
    }

    tweets.clear(); // free memory
    console.log("✅ Download queued");
  } catch (error) {
    console.error("❌ Download failed:", error);
    console.error("Error details:", {
      name: error.name,
      message: error.message,
      stack: error.stack,
    });
    throw error; // Re-throw to propagate to caller
  }
}

// ──────────────────────── message router ─────────────────────────
browser.runtime.onMessage.addListener(async (msg, sender, sendResponse) => {
  const tabId = msg.tabId ?? sender.tab?.id;
  try {
    switch (msg.cmd) {
      case "START": {
        // Get current active tab (don't force navigation - let user be on any timeline)
        const [tab] = await browser.tabs.query({
          active: true,
          currentWindow: true,
        });

        // scroller.js is auto-injected via manifest; just kick it off
        browser.tabs.sendMessage(tabId, {
          cmd: "SCROLL_START",
          maxScrolls: msg.maxScrolls,
        });

        sendResponse({ success: true });
        break;
      }

      case "PROCESS_TIMELINE_DATA": {
        const page = JSON.parse(msg.data);
        const tl = extractTimeline(page);
        if (!tl) return sendResponse({ success: false });

        let added = 0;
        for (const instr of tl.instructions ?? []) {
          if (instr.type !== "TimelineAddEntries") continue;
          for (const ent of instr.entries ?? []) {
            const tw = ent.content?.itemContent?.tweet_results?.result;
            if (tw?.__typename === "Tweet" && !tweets.has(tw.rest_id)) {
              tweets.set(tw.rest_id, flatten(tw));
              added++;
            }
          }
        }
        console.log(`📝 +${added} tweets (total ${tweets.size})`);
        sendResponse({ success: true, added, total: tweets.size });
        break;
      }

      case "STOP":
        browser.tabs.sendMessage(tabId, { cmd: "SCROLL_STOP" });
        await download();
        sendResponse({ success: true });
        break;

      case "SCROLL_FINISHED":
        console.log("🔔 Scroller reported completion");
        await download();
        sendResponse({ success: true });
        break;

      case "CLEAR":
        tweets.clear();
        sendResponse({ success: true });
        break;
    }
  } catch (e) {
    console.error(e);
    sendResponse({ success: false, error: e.message });
  }
  return true; // keep message port open
});
