// interceptor.js — isolated world, document_start
/* global chrome */

// Cross-browser compatibility: alias chrome to browser
if (typeof browser === "undefined") {
  var browser = chrome;
}

// 1️⃣ Inject the main-world patch as external script - bypasses CSP
(function injectPatch() {
  console.log("🔧 INTERCEPTOR: Starting external script injection...");
  console.log("🔧 INTERCEPTOR: Current URL:", window.location.href);
  console.log("🔧 INTERCEPTOR: Document readyState:", document.readyState);
  
  const s = document.createElement("script");
  s.src = browser.runtime.getURL("page_patch.js");
  s.type = "text/javascript";
  s.onload = () => {
    console.log("✅ INTERCEPTOR: External script loaded and executed");
    s.remove(); // tidy up after execution
    console.log("🧹 INTERCEPTOR: Script element removed");
    
    // Verify the script ran
    setTimeout(() => {
      console.log("🔍 INTERCEPTOR: Checking if interceptor installed:", window.__timelineInterceptorInstalled);
    }, 100);
  };
  s.onerror = (err) => {
    console.error("❌ INTERCEPTOR: External script failed to load:", err);
  };
  
  const target = document.documentElement || document.head || document.body;
  console.log("📍 INTERCEPTOR: Injecting external script into:", target?.tagName || "no target found");
  
  if (!target) {
    console.error("❌ INTERCEPTOR: No injection target found!");
    return;
  }
  
  target.appendChild(s);
  console.log("✅ INTERCEPTOR: External script injection initiated");
})();

// 2️⃣ Forward TIMELINE_RESPONSE messages to the background worker
if (!window.__timelineForwarderInstalled) {
  window.__timelineForwarderInstalled = true;
  console.log("🔗 INTERCEPTOR: Setting up message listener for TIMELINE_RESPONSE...");
  
  window.addEventListener("message", (e) => {
    if (e.source !== window) return;
    if (e.data?.type === "TIMELINE_RESPONSE") {
      console.log("📨 INTERCEPTOR: Forwarding TIMELINE_RESPONSE to background:", e.data.url);
      browser.runtime.sendMessage({
        cmd: "PROCESS_TIMELINE_DATA",
        data: e.data.data,
        url: e.data.url,
      }).then(response => {
        console.log("✅ INTERCEPTOR: Background processed message:", response);
      }).catch(err => {
        console.error("❌ INTERCEPTOR: Background message failed:", err);
      });
    } else if (e.data?.type) {
      console.log("📭 INTERCEPTOR: Received non-target message:", e.data.type);
    }
  });
  console.log("✅ INTERCEPTOR: Timeline relay → service-worker ready");
  
  // Test message relay after a delay
  setTimeout(() => {
    console.log("🧪 INTERCEPTOR: Testing message relay...");
    window.postMessage({ type: "TEST_MESSAGE", test: true }, "*");
  }, 2000);
}
