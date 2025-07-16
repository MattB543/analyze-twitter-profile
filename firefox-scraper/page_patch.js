// page_patch.js — runs in the page's main world
(() => {
  if (window.__timelineInterceptorInstalled) return;
  window.__timelineInterceptorInstalled = true;

  const relay = (data, url, method) => {
    console.log(`📡 Relaying ${method} data from:`, url);
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
      console.log("🔍 GraphQL request:", url);
      if (/UserTweets|LikesTimeline|TweetDetail|bookmark|HomeTimeline/i.test(url)) {
      res
        .clone()
        .text()
        .then((txt) => {
          console.log("✅ Captured GraphQL response via fetch:", url);
          relay(txt, url, "fetch");
        })
        .catch(err => console.error("❌ Failed to process GraphQL response:", err));
      } else {
        console.log("❌ GraphQL request not captured (no match):", url);
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
      console.log("🔍 GraphQL XHR request:", this._url);
      if (/UserTweets|LikesTimeline|TweetDetail|bookmark|HomeTimeline/i.test(this._url)) {
        console.log("✅ Setting up XHR interception for:", this._url);
        const _onreadystatechange = this.onreadystatechange;
        this.onreadystatechange = function () {
          if (this.readyState === 4 && this.status === 200) {
            console.log("✅ Captured GraphQL response via XHR:", this._url);
            relay(this.responseText, this._url, "xhr");
          }
          _onreadystatechange?.apply(this, arguments);
        };
      } else {
        console.log("❌ GraphQL XHR request not captured (no match):", this._url);
      }
    } else {
      console.log("🔍 Non-GraphQL XHR request:", this._url);
    }
    return origSend.apply(this, body);
  };

  console.log("✅ Timeline interceptor installed (main world)");
})();
