// page_patch.js — runs in the page's main world
(() => {
  if (window.__timelineInterceptorInstalled) return;
  window.__timelineInterceptorInstalled = true;

  const relay = (data, url, method) =>
    window.postMessage({ type: "TIMELINE_RESPONSE", data, url, method }, "*");

  /* ── fetch ─────────────────────────────────────────── */
  const origFetch = window.fetch;
  window.fetch = async function (...args) {
    const res = await origFetch.apply(this, args);
    const url = args[0];
    if (
      typeof url === "string" &&
      url.includes("/api/graphql/") &&
      /UserTweets|LikesTimeline|TweetDetail|bookmark/i.test(url)
    ) {
      res
        .clone()
        .text()
        .then((txt) => relay(txt, url, "fetch"));
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
    if (
      this._url?.includes("/api/graphql/") &&
      /UserTweets|LikesTimeline|TweetDetail|bookmark/i.test(this._url)
    ) {
      const _onreadystatechange = this.onreadystatechange;
      this.onreadystatechange = function () {
        if (this.readyState === 4 && this.status === 200)
          relay(this.responseText, this._url, "xhr");
        _onreadystatechange?.apply(this, arguments);
      };
    }
    return origSend.apply(this, body);
  };

  console.log("✅ Timeline interceptor installed (main world)");
})();
