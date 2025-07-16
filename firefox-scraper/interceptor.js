// interceptor.js — isolated world, document_start
/* global chrome */

// Cross-browser compatibility: alias chrome to browser
if (typeof browser === "undefined") {
  var browser = chrome;
}

// 1️⃣ Inject the main-world patch **inline**, bypassing CSP
(function injectPatch() {
  console.log("🔧 Starting inline script injection...");
  fetch(browser.runtime.getURL("page_patch.js"))
    .then(r => {
      console.log("✅ Fetched page_patch.js, status:", r.status);
      return r.text();
    })
    .then(code => {
      console.log("✅ Got script content, length:", code.length);
      const s = document.createElement("script");
      s.textContent = code + '\n//# sourceURL=twitter-exporter/page_patch.js';
      const target = document.documentElement || document.head || document.body;
      console.log("📍 Injecting into:", target.tagName);
      target.appendChild(s);
      console.log("✅ Script injected successfully");
      s.remove();
      console.log("🧹 Script element removed");
    })
    .catch(err => {
      console.error("❌ Script injection failed:", err);
    });
})();

// 2️⃣ Forward TIMELINE_RESPONSE messages to the background worker
if (!window.__timelineForwarderInstalled) {
  window.__timelineForwarderInstalled = true;
  console.log("🔗 Setting up message listener for TIMELINE_RESPONSE...");
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
    } else if (e.data?.type) {
      console.log("📭 Received non-target message:", e.data.type);
    }
  });
  console.log("✅ Timeline relay → service-worker ready");
}
