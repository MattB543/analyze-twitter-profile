/* global chrome */

// Cross-browser compatibility: alias chrome to browser
if (typeof browser === "undefined") {
  var browser = chrome;
}

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

start.onclick = () => {
  // Prevent double-click issues
  start.disabled = true;
  start.textContent = "⏳ Starting...";
  
  const scrollCountInput = document.getElementById("scrollCount");
  const scrollCount = scrollCountInput.value.trim();
  // Convert to number if provided, otherwise null for unlimited
  const maxScrolls = scrollCount ? parseInt(scrollCount, 10) : null;
  send("START", { maxScrolls });
};
stop.onclick = () => send("STOP");
clear.onclick = () => send("CLEAR");
