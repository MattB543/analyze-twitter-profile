// Cross-browser compatibility: alias chrome to browser
if (typeof browser === "undefined") {
  var browser = chrome;
}

// ——————————— scrolling util ——————————
const DELAY = 1500; // ms between scrolls
const IDLE_LIMIT = 6; // no-growth cycles before auto-stop

let scrolling = false;
let idleCycles = 0;
let lastHeight = 0;
let scrollCount = 0;
let maxScrolls = null; // user-defined limit, null for unlimited

function performScroll() {
  if (!scrolling) return;

  const h = document.documentElement.scrollHeight;
  idleCycles = h <= lastHeight + 100 ? idleCycles + 1 : 0;
  lastHeight = h;

  if (idleCycles >= IDLE_LIMIT) {
    console.log("🔚 No new content – auto-stop");
    stopScrolling();
    browser.runtime.sendMessage({ cmd: "SCROLL_FINISHED" });
    return;
  }

  window.scrollBy(0, window.innerHeight * 2);
  scrollCount++;

  const scrollMessage = maxScrolls
    ? `📜 Scroll ${scrollCount}/${maxScrolls}`
    : `📜 Scroll ${scrollCount} (unlimited)`;
  console.log(scrollMessage);

  // Check if we've reached the user-defined limit (if set)
  if (maxScrolls && scrollCount >= maxScrolls) {
    console.log(`🔚 Reached maximum scrolls (${maxScrolls}) – auto-stop`);
    stopScrolling();
    browser.runtime.sendMessage({ cmd: "SCROLL_FINISHED" });
    return;
  }

  setTimeout(performScroll, DELAY);
}

function startScrolling(userMaxScrolls = null) {
  if (scrolling) return;
  maxScrolls = userMaxScrolls;
  const limitMessage = maxScrolls
    ? ` (max ${maxScrolls} scrolls)`
    : " (unlimited)";
  console.log(`🚀 Scrolling started${limitMessage}`);
  scrolling = true;
  idleCycles = 0;
  lastHeight = 0;
  scrollCount = 0;
  setTimeout(performScroll, 500);
}

function stopScrolling() {
  scrolling = false;
  console.log("🛑 Scrolling stopped");
}

browser.runtime.onMessage.addListener((msg, _s, sendResponse) => {
  if (msg.cmd === "SCROLL_START") startScrolling(msg.maxScrolls);
  if (msg.cmd === "SCROLL_STOP") stopScrolling();
  sendResponse({ success: true });
  return true;
});
