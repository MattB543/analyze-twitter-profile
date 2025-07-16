// interceptor.js — isolated world, document_start
/* global chrome */

// Cross-browser compatibility: alias chrome to browser
if (typeof browser === "undefined") {
  var browser = chrome;
}

// 1️⃣ Inject the main-world patch **inline**, bypassing CSP
(function injectPatch() {
  fetch(browser.runtime.getURL("page_patch.js"))
    .then(r => r.text())
    .then(code => {
      const s = document.createElement("script");
      s.textContent = code + '\n//# sourceURL=twitter-exporter/page_patch.js';
      (document.documentElement || document.head).appendChild(s);
      s.remove();
    })
    .catch(console.error);
})();

// 2️⃣ Forward TIMELINE_RESPONSE messages to the background worker
if (!window.__timelineForwarderInstalled) {
  window.__timelineForwarderInstalled = true;
  window.addEventListener("message", (e) => {
    if (e.source !== window) return;
    if (e.data?.type === "TIMELINE_RESPONSE") {
      console.log("📨 Forwarding TIMELINE_RESPONSE to background:", e.data.url);
      browser.runtime.sendMessage({
        cmd: "PROCESS_TIMELINE_DATA",
        data: e.data.data,
        url: e.data.url,
      }).then(response => {
        console.log("✅ Background processed message:", response);
      }).catch(err => {
        console.error("❌ Background message failed:", err);
      });
    }
  });
  console.log("✅ Timeline relay → service-worker ready");
}
