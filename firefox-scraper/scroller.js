// Cross-browser compatibility: alias chrome to browser
if (typeof browser === "undefined") {
  var browser = chrome;
}

// ——————————— scrolling util ——————————
const BASE_DELAY = 1800; // base ms between scrolls
const DELAY_VARIANCE = 800; // random variance ±400ms
const IDLE_LIMIT = 6; // no-growth cycles before auto-stop

let scrolling = false;
let idleCycles = 0;
let lastHeight = 0;
let scrollCount = 0;
let maxScrolls = null; // user-defined limit, null for unlimited

// Human-like randomization helpers
function randomDelay() {
  return BASE_DELAY + (Math.random() - 0.5) * DELAY_VARIANCE;
}

function randomScrollAmount() {
  // Vary scroll amount between 1.5x and 2.5x viewport height
  const baseScroll = window.innerHeight;
  const variance = 0.5; // ±50% variance
  return baseScroll * (1.5 + Math.random() * variance);
}

function shouldPause() {
  // 15% chance to pause for extra time (simulating reading)
  return Math.random() < 0.15;
}

function randomPauseDuration() {
  // Pause for 3-8 seconds when "reading"
  return 3000 + Math.random() * 5000;
}

function performScroll() {
  if (!scrolling) return;

  const h = document.documentElement.scrollHeight;
  idleCycles = h <= lastHeight + 100 ? idleCycles + 1 : 0;
  lastHeight = h;

  if (idleCycles >= IDLE_LIMIT) {
    console.log("🔚 No new content – auto-stop");
    stopScrolling();
    try {
      browser.runtime.sendMessage({ cmd: "SCROLL_FINISHED" });
    } catch (e) {
      console.warn("Could not send SCROLL_FINISHED (tab may be closed):", e);
    }
    return;
  }

  // Randomized scroll amount
  const scrollAmount = randomScrollAmount();
  window.scrollBy(0, scrollAmount);
  scrollCount++;

  const scrollMessage = maxScrolls
    ? `📜 Scroll ${scrollCount}/${maxScrolls} (${Math.round(scrollAmount)}px)`
    : `📜 Scroll ${scrollCount} (${Math.round(scrollAmount)}px, unlimited)`;
  console.log(scrollMessage);

  // Check if we've reached the user-defined limit (if set)
  if (maxScrolls && scrollCount >= maxScrolls) {
    console.log(`🔚 Reached maximum scrolls (${maxScrolls}) – auto-stop`);
    stopScrolling();
    try {
      browser.runtime.sendMessage({ cmd: "SCROLL_FINISHED" });
    } catch (e) {
      console.warn("Could not send SCROLL_FINISHED (tab may be closed):", e);
    }
    return;
  }

  // Determine next delay with occasional pauses
  let nextDelay = randomDelay();
  if (shouldPause()) {
    nextDelay += randomPauseDuration();
    console.log(`⏸️ Random pause: ${Math.round(nextDelay/1000)}s`);
  }

  setTimeout(performScroll, nextDelay);
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
  
  // Random initial delay (1-3 seconds) to mimic user behavior
  const initialDelay = 1000 + Math.random() * 2000;
  console.log(`⏳ Starting in ${Math.round(initialDelay/1000)}s...`);
  setTimeout(performScroll, initialDelay);
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
