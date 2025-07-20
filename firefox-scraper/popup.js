/* global chrome */

// Cross-browser compatibility: alias chrome to browser
if (typeof browser === "undefined") {
  var browser = chrome;
}

// Get explicit DOM handles instead of relying on implicit globals
const startBtn = document.getElementById("start");
const stopBtn = document.getElementById("stop");  
const clearBtn = document.getElementById("clear");
const scrollCountInput = document.getElementById("scrollCount");
const scopeTweets = document.getElementById("scopeTweets");
const scopeBookmarks = document.getElementById("scopeBookmarks");
const scopeLikes = document.getElementById("scopeLikes");
const scopeReplies = document.getElementById("scopeReplies");

async function send(cmd, data = {}) {
  try {
    const [tab] = await browser.tabs.query({
      active: true,
      currentWindow: true,
    });
    // sendMessage returns a Promise in MV3 if you omit the callback
    await browser.runtime.sendMessage({ cmd, tabId: tab.id, ...data });
  } catch (e) {
    // benign if the background closed the port first
    console.debug("popup send() warning:", e.message);
  } finally {
    window.close(); // close only after (attempted) reply
  }
}

startBtn.onclick = () => {
  startBtn.disabled = true;
  startBtn.textContent = "⏳ Starting…";

  const maxScrolls = scrollCountInput.value.trim()
    ? parseInt(scrollCountInput.value, 10)
    : null;

  /* keep the visual order: tweets → bookmarks → likes → replies */
  const scopes = [];
  if (scopeTweets.checked)    scopes.push("tweets");
  if (scopeBookmarks.checked) scopes.push("bookmarks");
  if (scopeLikes.checked)     scopes.push("likes");
  if (scopeReplies.checked)   scopes.push("replies");

  if (!scopes.length) {       // nothing selected
    alert("Pick at least one scope to capture!");
    startBtn.disabled = false;
    startBtn.textContent = "▶️ Start capture";
    return;
  }

  send("START", { maxScrolls, scopes });
};
stopBtn.onclick = () => send("STOP");
clearBtn.onclick = () => send("CLEAR");
