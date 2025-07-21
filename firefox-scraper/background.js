/* global chrome */

// Cross-browser compatibility: alias chrome to browser
if (typeof browser === "undefined") {
  var browser = chrome;
}

// ───────────────────────────── helpers ─────────────────────────────
function flatten(tweet) {
  const l = tweet.legacy ?? {};
  const u = tweet?.core?.user_results?.result?.legacy ?? {};
  const quoted = tweet?.quoted_status_result?.result;
  const retweeted = tweet?.retweeted_status_result?.result;
  return {
    tweet_id: tweet.rest_id,
    created_at: l.created_at,
    text: l.full_text || l.text,
    lang: l.lang,
    favorite: l.favorite_count,
    retweet: l.retweet_count,
    reply: l.reply_count,
    quote: l.quote_count,
    // Consistent aliases for analytics
    favorite_count: l.favorite_count,
    retweet_count: l.retweet_count,
    reply_count: l.reply_count,
    quote_count: l.quote_count,
    user_handle: u.screen_name,
    parent_ids: [
      ...(l.referenced_tweets ?? []).map((r) => r.id_str),
      l.in_reply_to_status_id_str,
      l.quoted_status_id_str,
      quoted?.rest_id, // nested quote
      ...(retweeted?.rest_id ? [retweeted.rest_id] : []), // retweeted original
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
  if (!obj || typeof obj !== "object") return null;

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

// ───────────── batch‑run state ─────────────
let scopeQueue = []; // e.g. ["tweets","bookmarks",…]
let currentScope = null;
let startParams = {}; // keeps original maxScrolls etc.

// Remember the initially extracted username so we can still build correct
// URLs after we navigate away from the profile page (e.g. when we are on
// /i/bookmarks the first path segment would be "i").
let savedUsername = null;

function makeUrl(scope, baseUrl) {
  console.log(
    `🔧 makeUrl called with scope: "${scope}", baseUrl: "${baseUrl}"`
  );
  // Try to extract the first path segment after the domain – this should be
  // the username when we are on a user page.
  const m = baseUrl.match(/^https:\/\/(?:x|twitter)\.com\/([^\/?#]+)/i);
  let extracted = m ? m[1] : null;

  // Some path segments are *not* usernames but reserved Twitter routes.
  // If we encounter one of those (e.g. "i", "home", …) we will ignore it and
  // keep using the previously remembered username if we have one.
  const RESERVED = new Set([
    "i",
    "home",
    "explore",
    "notifications",
    "messages",
    "settings",
    "search",
  ]);

  if (extracted && !RESERVED.has(extracted)) {
    savedUsername = extracted; // keep for later scopes
  }

  const username = savedUsername;

  console.log(`👤 Extracted username: "${extracted}" → using: "${username}"`);

  if (!username) {
    console.log(`⚠️ No username available, returning baseUrl: "${baseUrl}"`);
    return baseUrl; // fallback when we really have nothing
  }

  let resultUrl;
  switch (scope) {
    case "tweets":
      resultUrl = `https://x.com/${username}`;
      break;
    case "replies":
      resultUrl = `https://x.com/${username}/with_replies`;
      break;
    case "likes":
      resultUrl = `https://x.com/${username}/likes`;
      break;
    case "bookmarks":
      resultUrl = `https://x.com/i/bookmarks`;
      break;
    default:
      resultUrl = baseUrl;
      break;
  }

  console.log(`🎯 makeUrl result: "${resultUrl}"`);
  return resultUrl;
}

async function beginScope(tabId, scope) {
  currentScope = scope;
  console.log(`🚩 Starting scope: ${scope}`);

  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  const targetUrl = makeUrl(scope, tab.url);

  console.log(`📍 Current URL: ${tab.url}`);
  console.log(`📍 Target URL: ${targetUrl}`);
  console.log(`🔀 URLs match: ${tab.url === targetUrl}`);

  // navigate only if we're not already there
  if (tab.url !== targetUrl) {
    console.log(`🚀 Navigating to: ${targetUrl}`);
    await browser.tabs.update(tabId, { url: targetUrl });
  } else {
    // ensure full reload so interceptor sees first page
    console.log(`🔄 Reloading current page: ${tab.url}`);
    browser.tabs.reload(tabId, { bypassCache: true });
  }

  /* same one‑shot onUpdated trick as before */
  const onUpdated = (updatedId, info) => {
    if (updatedId === tabId && info.status === "complete") {
      browser.tabs.onUpdated.removeListener(onUpdated);
      console.log("✅ Page ready – launching scroller");
      browser.tabs
        .sendMessage(tabId, {
          cmd: "SCROLL_START",
          maxScrolls: startParams.maxScrolls,
        })
        .catch((err) => console.error("❌ SCROLL_START failed:", err));
    }
  };
  browser.tabs.onUpdated.addListener(onUpdated);
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
  console.log(`📊 Current tweets in memory:`, [...tweets.keys()].slice(0, 5)); // Show first 5 IDs
  if (tweets.size === 0) {
    console.warn("⚠️  No tweets captured!");
    return;
  }

  // Prefer the scope we are currently processing; fall back to URL heuristics
  let scope = currentScope || "bookmarks"; // default fallback remains "bookmarks"

  // If currentScope is not set (e.g. manual download with no active batch run)
  if (!currentScope) {
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
        } else if (/\/with_replies(?:\/|$|\?)/i.test(tab.url)) {
          scope = "replies";
        } else if (
          /^https:\/\/(twitter|x)\.com\/[^\/?#]+(?:\/?$|\?)/i.test(tab.url)
        ) {
          scope = "tweets";
        }
      }
    } catch (e) {
      console.warn(
        "Could not determine page type from URL, keeping default scope",
        e
      );
    }
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
        tweets.clear();

        // remember params & queue
        scopeQueue = Array.isArray(msg.scopes) ? [...msg.scopes] : ["tweets"];
        startParams = { maxScrolls: msg.maxScrolls };

        await beginScope(tabId, scopeQueue.shift()); // kicks off first run
        sendResponse({ success: true });
        break;
      }

      case "PROCESS_TIMELINE_DATA": {
        console.log("🔄 Processing timeline data from:", msg.url);
        try {
          const page = JSON.parse(msg.data);
          console.log("✅ JSON parsed successfully");

          const tl = extractTimeline(page);
          if (!tl) {
            console.log("❌ No timeline found in response");
            return sendResponse({ success: false, error: "No timeline found" });
          }
          console.log(
            "✅ Timeline extracted, instructions:",
            tl.instructions?.length || 0
          );

          let added = 0;
          let processed = 0;
          let skipped = 0;

          for (const instr of tl.instructions ?? []) {
            console.log(`🔍 Processing instruction type: ${instr.type}`);

            // Handle cache clearing instruction
            if (instr.type === "TimelineClearCache") {
              console.log(
                "🧹 TimelineClearCache instruction - clearing tweet cache"
              );
              tweets.clear();
              continue;
            }

            if (
              ![
                "TimelineAddEntries",
                "TimelineReplaceEntry",
                "TimelineAddToModule",
                "TimelineTerminateTimeline",
                "TimelinePinEntry",
              ].includes(instr.type)
            ) {
              console.log(`⏭️ Skipping instruction type: ${instr.type}`);
              continue;
            }

            const root =
              instr.entries ??
              (instr.entry ? [instr.entry] : instr.module?.items ?? []) ??
              []; // defensively default to empty array

            console.log(
              `📋 Processing ${root.length} entries for instruction: ${instr.type}`
            );

            // Use queue to handle nested modules without iterator issues
            const queue = [...root]; // breadth‑first walk
            while (queue.length) {
              const ent = queue.shift();
              processed++;

              // 1) wrapper → enqueue its children and move on
              if (ent?.content?.items?.length) {
                console.log(
                  `📦 Found wrapper with ${ent.content.items.length} items, expanding queue`
                );
                queue.push(...ent.content.items);
                continue;
              }
              if (ent?.item?.items?.length) {
                console.log(
                  `📦 Found item wrapper with ${ent.item.items.length} items, expanding queue`
                );
                queue.push(...ent.item.items);
                continue;
              }

              // Normalize the path to itemContent (gallery-dl approach)
              // Works for both ent.content.itemContent and ent.item.itemContent
              const itemContent = (ent.content ?? ent.item)?.itemContent;
              const tw = itemContent?.tweet_results?.result;

              const entryType =
                ent.entryId || ent.content?.entryType || "unknown";

              if (tw?.__typename === "Tweet") {
                if (!tweets.has(tw.rest_id)) {
                  tweets.set(tw.rest_id, flatten(tw));
                  added++;
                  console.log(
                    `✅ Added tweet: ${tw.rest_id} (from ${entryType})`
                  );
                } else {
                  console.log(
                    `⚠️ Tweet already exists: ${tw.rest_id} (from ${entryType})`
                  );
                }
              } else {
                // Only log detailed info for unexpected entry types (not cursors or known structural elements)
                if (
                  !entryType.includes("cursor") &&
                  !entryType.includes("who-to-follow") &&
                  !entryType.includes("profile-conversation") &&
                  !entryType.includes("module") &&
                  itemContent
                ) {
                  console.log(
                    `🔍 Unexpected entry type with itemContent: ${entryType}`,
                    {
                      itemContentKeys: Object.keys(itemContent || {}),
                      tweetResults: itemContent?.tweet_results,
                      typename: tw?.__typename,
                    }
                  );
                } else if (entryType.includes("cursor")) {
                  console.log(`📄 Cursor entry: ${entryType}`);
                } else {
                  skipped++;
                  console.log(`⏭️ Skipping structural entry: ${entryType}`);
                }
              }
            }
          }
          console.log(
            `📊 Processing summary: ${processed} entries processed, ${added} tweets added, ${skipped} entries skipped`
          );
          console.log(`📝 +${added} tweets (total ${tweets.size})`);
          sendResponse({ success: true, added, total: tweets.size });
        } catch (e) {
          console.error("❌ Error processing timeline data:", e);
          sendResponse({ success: false, error: e.message });
        }
        break;
      }

      case "STOP":
        browser.tabs.sendMessage(tabId, { cmd: "SCROLL_STOP" });
        await download();

        // move on or finish
        if (scopeQueue.length) {
          tweets.clear(); // wipe previous data
          await beginScope(tabId, scopeQueue.shift());
        } else {
          console.log("🎉 Batch run complete");
        }

        sendResponse({ success: true });
        break;

      case "SCROLL_FINISHED":
        console.log("🔔 Scroller reported completion");
        await download();

        // move on or finish
        if (scopeQueue.length) {
          tweets.clear(); // wipe previous data
          await beginScope(tabId, scopeQueue.shift());
        } else {
          console.log("🎉 Batch run complete");
        }

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
