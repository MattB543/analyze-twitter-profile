// page_patch.js — runs in the page's main world
(() => {
  if (window.__timelineInterceptorInstalled) {
    console.log("⚠️ Timeline interceptor already installed, skipping");
    return;
  }
  console.log("🚀 Timeline interceptor starting installation...");
  window.__timelineInterceptorInstalled = true;

  const DEBUG = false; // Set to true for development logging
  
  const relay = (data, url, method) => {
    if (DEBUG) console.log(`📡 Relaying ${method} data from:`, url);
    window.postMessage({ type: "TIMELINE_RESPONSE", data, url, method }, "*");
  };

  /* ── fetch ─────────────────────────────────────────── */
  const origFetch = window.fetch;
  window.fetch = async function (...args) {
    const res = await origFetch.apply(this, args);
    const url = args[0];
    if (
      typeof url === "string" &&
      url.includes("/api/graphql/")
    ) {
      // Debug: log all GraphQL requests to see what we're missing
      if (DEBUG) console.log("🔍 GraphQL request:", url);
      const isTargetEndpoint = /UserTweets|UserTweetsAndReplies|UserMedia|Likes(?:Timeline)?|BookmarkedTweetsTimeline|Bookmarks(?:Timeline)?|Home(?:Latest)?Timeline|TweetDetail/i.test(url);
      if (DEBUG) console.log(`📋 Target endpoint match: ${isTargetEndpoint}`);
      if (isTargetEndpoint) {
      res
        .clone()
        .text()
        .then((txt) => {
          if (DEBUG) console.log("✅ Captured GraphQL response via fetch:", url);
          relay(txt, url, "fetch");
        })
        .catch(err => { if (DEBUG) console.error("❌ Failed to process GraphQL response:", err); });
      } else {
        if (DEBUG) console.log("❌ GraphQL request not captured (no match):", url);
      }
    }
    return res;
  };

  /* ── XHR ────────────────────────────────────────────── */
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (m, u, ...rest) {
    this._url = u;
    return origOpen.apply(this, [m, u, ...rest]);
  };

  XMLHttpRequest.prototype.send = function (...body) {
    if (this._url?.includes("/api/graphql/")) {
      if (DEBUG) console.log("🔍 GraphQL XHR request:", this._url);
      const isTargetEndpoint = /UserTweets|UserTweetsAndReplies|UserMedia|Likes(?:Timeline)?|BookmarkedTweetsTimeline|Bookmarks(?:Timeline)?|Home(?:Latest)?Timeline|TweetDetail/i.test(this._url);
      if (DEBUG) console.log(`📋 XHR Target endpoint match: ${isTargetEndpoint}`);
      if (isTargetEndpoint) {
        if (DEBUG) console.log("✅ Setting up XHR interception for:", this._url);
        const _onreadystatechange = this.onreadystatechange;
        this.onreadystatechange = function () {
          if (this.readyState === 4 && this.status === 200) {
            if (DEBUG) console.log("✅ Captured GraphQL response via XHR:", this._url);
            relay(this.responseText, this._url, "xhr");
          }
          _onreadystatechange?.apply(this, arguments);
        };
      } else {
        if (DEBUG) console.log("❌ GraphQL XHR request not captured (no match):", this._url);
      }
    } else {
      if (DEBUG) console.log("🔍 Non-GraphQL XHR request:", this._url);
    }
    return origSend.apply(this, body);
  };

  console.log("✅ Timeline interceptor installed (main world)");
  console.log("🔍 Testing fetch interception...");
  
  // Test if our interception is working
  setTimeout(() => {
    console.log("🧪 Original fetch function:", typeof window.fetch);
    console.log("🧪 Original XHR function:", typeof window.XMLHttpRequest);
  }, 1000);
})();
