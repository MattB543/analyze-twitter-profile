// page_patch.js — runs in the page's main world
(() => {
  if (window.__timelineInterceptorInstalled) {
    console.log(
      "⚠️ PAGE_PATCH: Timeline interceptor already installed, skipping"
    );
    return;
  }
  console.log("🚀 PAGE_PATCH: Timeline interceptor starting installation...");
  console.log("🔍 PAGE_PATCH: Current URL:", window.location.href);
  console.log("🔍 PAGE_PATCH: Original fetch type:", typeof window.fetch);
  console.log(
    "🔍 PAGE_PATCH: Original XMLHttpRequest type:",
    typeof window.XMLHttpRequest
  );

  window.__timelineInterceptorInstalled = true;

  const DEBUG = true; // Set to true for development logging

  const relay = (data, url, method) => {
    if (DEBUG) console.log(`📡 PAGE_PATCH: Relaying ${method} data from:`, url);
    window.postMessage({ type: "TIMELINE_RESPONSE", data, url, method }, "*");
  };

  /* ── fetch ─────────────────────────────────────────── */
  const origFetch = window.fetch;
  console.log("🔧 PAGE_PATCH: Patching fetch function...");

  window.fetch = async function (...args) {
    const res = await origFetch.apply(this, args);
    const url = args[0];

    if (DEBUG) console.log("🌐 PAGE_PATCH: Fetch intercepted:", url);

    if (typeof url === "string" && url.includes("/api/graphql/")) {
      // Debug: log all GraphQL requests to see what we're missing
      if (DEBUG) console.log("🔍 PAGE_PATCH: GraphQL request detected:", url);
      const isTargetEndpoint =
        /UserTweets|UserTweetsAndReplies|UserMedia|Likes(?:Timeline)?|BookmarkedTweetsTimeline|Bookmarks(?:Timeline)?|Home(?:Latest)?Timeline|TweetDetail/i.test(
          url
        );
      if (DEBUG)
        console.log(
          `📋 PAGE_PATCH: Target endpoint match: ${isTargetEndpoint}`
        );
      if (isTargetEndpoint) {
        res
          .clone()
          .text()
          .then((txt) => {
            if (DEBUG)
              console.log(
                "✅ PAGE_PATCH: Captured GraphQL response via fetch:",
                url
              );
            relay(txt, url, "fetch");
          })
          .catch((err) => {
            if (DEBUG)
              console.error(
                "❌ PAGE_PATCH: Failed to process GraphQL response:",
                err
              );
          });
      } else {
        if (DEBUG)
          console.log(
            "❌ PAGE_PATCH: GraphQL request not captured (no match):",
            url
          );
      }
    }
    return res;
  };
  console.log("✅ PAGE_PATCH: Fetch function patched");

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
      const isTargetEndpoint =
        /UserTweets|UserTweetsAndReplies|UserMedia|Likes(?:Timeline)?|BookmarkedTweetsTimeline|Bookmarks(?:Timeline)?|Home(?:Latest)?Timeline|TweetDetail/i.test(
          this._url
        );
      if (DEBUG)
        console.log(`📋 XHR Target endpoint match: ${isTargetEndpoint}`);
      if (isTargetEndpoint) {
        if (DEBUG)
          console.log("✅ Setting up XHR interception for:", this._url);
        const _onreadystatechange = this.onreadystatechange;
        this.onreadystatechange = function () {
          if (this.readyState === 4 && this.status === 200) {
            if (DEBUG)
              console.log("✅ Captured GraphQL response via XHR:", this._url);
            relay(this.responseText, this._url, "xhr");
          }
          _onreadystatechange?.apply(this, arguments);
        };
      } else {
        if (DEBUG)
          console.log(
            "❌ GraphQL XHR request not captured (no match):",
            this._url
          );
      }
    } else {
      if (DEBUG) console.log("🔍 Non-GraphQL XHR request:", this._url);
    }
    return origSend.apply(this, body);
  };

  console.log("✅ PAGE_PATCH: Timeline interceptor installed (main world)");
  console.log("🔍 PAGE_PATCH: Testing fetch interception...");

  // Test if our interception is working
  setTimeout(() => {
    console.log("🧪 PAGE_PATCH: Current fetch function:", typeof window.fetch);
    console.log(
      "🧪 PAGE_PATCH: Current XHR function:",
      typeof window.XMLHttpRequest
    );
    console.log(
      "🧪 PAGE_PATCH: Interceptor flag:",
      window.__timelineInterceptorInstalled
    );

    // Test a dummy fetch to see if our interceptor works
    if (DEBUG) {
      console.log("🧪 PAGE_PATCH: Testing dummy fetch...");
      fetch("https://example.com/test").catch(() => {
        console.log(
          "🧪 PAGE_PATCH: Dummy fetch test completed (expected to fail)"
        );
      });
    }
  }, 1000);
})();
