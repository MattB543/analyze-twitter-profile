### What’s going wrong

1. **`page_patch.js` never runs.**
   Twitter started enforcing a stricter CSP in early 2024. Because the extension injects the patch **inline** (`s.textContent = code`), the browser now blocks it with

   ````
   Content Security Policy: The page's settings blocked the loading of a resource at inline ("script‑src")
   ``` :contentReference[oaicite:0]{index=0}  

   No patch ⇒ no `TIMELINE_RESPONSE` messages ⇒ the background script’s tweet‑map stays empty, so the download is a 0‑byte file.

   ````

2. **Why it used to work:** extensions can still bypass CSP, **but only with a `<script src="moz‑extension://…">` tag** or via `browser.scripting.executeScript`. Inline code no longer slips through on twitter.com.

3. **Proof:** MDN explicitly notes that **web‑accessible extension files aren’t blocked by CSP**, whereas inline code can be. ([MDN Web Docs][1])

---

### Quick‑fix (two small edits)

| File               | Patch                                                                                                                                                                                                                                                                                                                               |   |               |   |                                                                                                                                                                                                                                                                                                                                                                                  |   |               |   |                                                 |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | - | ------------- | - | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | - | ------------- | - | ----------------------------------------------- |
| **manifest.json**  | `jsonc\n{\n  …,\n  \"web_accessible_resources\": [\n    \"page_patch.js\"\n  ]\n}\n`                                                                                                                                                                                                                                                |   |               |   |                                                                                                                                                                                                                                                                                                                                                                                  |   |               |   |                                                 |
| **interceptor.js** | \`\`\`diff\n- // fetch + inline textContent (blocked by CSP)\n- fetch(browser.runtime.getURL("page\_patch.js"))\n-   .then(r => r.text())\n-   .then(code => {\n-     const s = document.createElement("script");\n-     s.textContent = code + "\n//# sourceURL=twitter-exporter/page\_patch.js";\n-     (document.documentElement |   | document.head |   | document.body).appendChild(s);\n-     s.remove();\n-   });\n+ // load as external script – bypasses CSP\n+ (function injectPatch() {\n+   const s = document.createElement("script");\n+   s.src = browser.runtime.getURL("page\_patch.js");\n+   s.type = "text/javascript";\n+   s.onload = () => s.remove();        // tidy up after execution\n+   (document.documentElement |   | document.head |   | document.body).appendChild(s);\n+ })();\n\`\`\` |

**Why this works**

* A `<script src>` that points at a **web‑accessible extension resource** is exempt from both CORS and CSP checks. The code executes in the page’s main world exactly as before, but Twitter’s CSP no longer objects. ([MDN Web Docs][1])
* No other logic needs to change—the fetch/XHR monkey‑patch in `page_patch.js` begins capturing GraphQL again, the background script receives `PROCESS_TIMELINE_DATA`, and tweets fill the `Map`.

---

### After applying the fix

1. Reload the extension and open the console: you should now see the **`🚀 Timeline interceptor…`** log line from `page_patch.js` on first load.
2. Run *Start capture* → scrolls → *Stop & download* — the JSONL should contain \~40 KB for the first screen of bookmarks instead of “0 tweets”.

---

### Small follow‑ups for July 2025 (optional but recommended)

| Area                | Rationale                                                                                                                                                                                                                                               |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Regex tweaks**    | Twitter sometimes serves `BookmarkedTweetsTimeline` instead of `Bookmark*`. The existing pattern already covers it, but you might add `TimelineTerminateTimeline` and `TimelinePinEntry` to the instruction allow‑list to avoid console noise.          |
| **Timeline schema** | The new `tweet_results.result.tweet` wrapper hasn’t shown up on desktop yet, but it’s live on mobile web. A defensive check in `flatten()` (`const node = tw?.tweet ?? tw;`) keeps you future‑proof.                                                    |
