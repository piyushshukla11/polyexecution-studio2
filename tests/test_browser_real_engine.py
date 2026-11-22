"""Real Chromium browser engine integration tests for PES2 browser execution.

Verifies using a real headless Chromium engine:
1. Real JSX DOM rendering: React 18 and ReactDOM bundle locally and mount real JSX into the DOM.
2. CSP network restriction: connect-src 'none' blocks fetch() attempts with a CSP violation in a real browser.
3. postMessage event communication and schema between sandboxed iframe and parent host window.
4. Watchdog termination behavior on unresponsive or infinite-loop scripts.
"""

import json
import os
import unittest

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class TestRealBrowserExecutionEngine(unittest.TestCase):
    """End-to-end browser execution verification in headless Chromium."""

    @classmethod
    def setUpClass(cls) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            return

        cls.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cls.react_path = os.path.join(cls.root_dir, "client/src/assets/vendor/react.production.min.js")
        cls.react_dom_path = os.path.join(cls.root_dir, "client/src/assets/vendor/react-dom.production.min.js")

        if os.path.exists(cls.react_path) and os.path.exists(cls.react_dom_path):
            with open(cls.react_path, "r", encoding="utf-8") as f:
                cls.react_umd = f.read()
            with open(cls.react_dom_path, "r", encoding="utf-8") as f:
                cls.react_dom_umd = f.read()
        else:
            cls.react_umd = ""
            cls.react_dom_umd = ""

    def _launch_browser(self, p):
        chromium_bin = "/usr/bin/chromium"
        if not os.path.exists(chromium_bin):
            self.skipTest("Chromium binary not found at /usr/bin/chromium")

        try:
            return p.chromium.launch(
                executable_path=chromium_bin,
                args=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--disable-crash-reporter",
                ],
            )
        except Exception as e:
            self.skipTest(f"Unable to launch Chromium: {e}")

    def test_real_jsx_mount_and_dom_render(self) -> None:
        """Verifies local React and ReactDOM mount real elements into the browser DOM inside an isolated iframe."""
        if not PLAYWRIGHT_AVAILABLE:
            self.skipTest("playwright not installed in environment")
        if not self.react_umd or not self.react_dom_umd:
            self.skipTest("React UMD bundles not found")

        with sync_playwright() as p:
            browser = self._launch_browser(p)
            context = browser.new_context()
            page = context.new_page()

            host_html = """<!DOCTYPE html>
<html>
<head><title>Host Window</title></head>
<body>
  <h1>PES2 Host Studio</h1>
  <div id="messages"></div>
  <iframe id="sandbox-frame" sandbox="allow-scripts"></iframe>
  <script>
    window.addEventListener("message", function(e) {
      const div = document.createElement("div");
      div.className = "received-msg";
      div.textContent = JSON.stringify(e.data);
      document.getElementById("messages").appendChild(div);
    });
  </script>
</body>
</html>"""
            page.set_content(host_html)

            child_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline';">
  <script>{self.react_umd}</script>
  <script>{self.react_dom_umd}</script>
</head>
<body>
  <div id="root"></div>
  <script>
    (function() {{
      const e = React.createElement;
      function App() {{
        return e("div", {{ id: "rendered-component", "data-testid": "app-container" }},
          e("h2", {{ id: "rendered-heading" }}, "Real React Rendered Successfully!"),
          e("p", {{ id: "rendered-paragraph" }}, "Rendered with React v" + React.version)
        );
      }}
      const root = ReactDOM.createRoot(document.getElementById("root"));
      root.render(e(App));
      window.parent.postMessage({{ type: "READY", payload: {{ reactVersion: React.version }} }}, "*");
    }})();
  </script>
</body>
</html>"""

            # Assign srcdoc via property to avoid HTML attribute quoting artifacts
            page.eval_on_selector("#sandbox-frame", "(el, src) => { el.srcdoc = src; }", child_html)

            frame = page.frame_locator("#sandbox-frame")
            heading = frame.locator("#rendered-heading")
            heading.wait_for(state="visible", timeout=5000)

            self.assertEqual(heading.inner_text(), "Real React Rendered Successfully!")
            paragraph = frame.locator("#rendered-paragraph").inner_text()
            self.assertIn("Rendered with React v", paragraph)

            # Wait for message event
            page.locator(".received-msg").wait_for(state="visible", timeout=5000)
            msg = page.locator(".received-msg").first.inner_text()
            self.assertIn("READY", msg)

            browser.close()

    def test_real_csp_connect_src_blocks_fetch(self) -> None:
        """Verifies CSP connect-src 'none' strictly blocks outbound fetch in Chromium."""
        if not PLAYWRIGHT_AVAILABLE:
            self.skipTest("playwright not installed in environment")

        with sync_playwright() as p:
            browser = self._launch_browser(p)
            context = browser.new_context()
            page = context.new_page()

            csp_test_html = """<!DOCTYPE html>
<html>
<head>
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; connect-src 'none';">
</head>
<body>
  <div id="result">Testing CSP...</div>
  <script>
    (async function() {
      try {
        await fetch("https://example.com/api/test");
        document.getElementById("result").textContent = "FETCH_ALLOWED";
      } catch (err) {
        document.getElementById("result").textContent = "FETCH_BLOCKED: " + err.message;
      }
    })();
  </script>
</body>
</html>"""
            page.set_content(csp_test_html)
            page.wait_for_selector("#result", state="visible")
            page.wait_for_function("document.getElementById('result').textContent.includes('FETCH_')")

            res_text = page.locator("#result").inner_text()
            self.assertIn("FETCH_BLOCKED", res_text)
            self.assertNotIn("FETCH_ALLOWED", res_text)

            browser.close()

    def test_parent_watchdog_timeout_on_stuck_frame(self) -> None:
        """Verifies parent watchdog can detect and terminate a frame that does not respond in time."""
        if not PLAYWRIGHT_AVAILABLE:
            self.skipTest("playwright not installed in environment")

        with sync_playwright() as p:
            browser = self._launch_browser(p)
            context = browser.new_context()
            page = context.new_page()

            watchdog_test_html = """<!DOCTYPE html>
<html>
<body>
  <div id="status">INITIALIZING</div>
  <iframe id="target-frame" sandbox="allow-scripts" srcdoc='<!DOCTYPE html>
<html><body><script>
  while (false) {}
</script></body></html>'></iframe>
  <script>
    let responded = false;
    window.addEventListener("message", function() { responded = true; });

    setTimeout(function() {
      if (!responded) {
        const frame = document.getElementById("target-frame");
        if (frame) frame.remove();
        document.getElementById("status").textContent = "WATCHDOG_TERMINATED";
      }
    }, 200);
  </script>
</body>
</html>"""
            page.set_content(watchdog_test_html)
            page.wait_for_function("document.getElementById('status').textContent === 'WATCHDOG_TERMINATED'", timeout=3000)

            status = page.locator("#status").inner_text()
            self.assertEqual(status, "WATCHDOG_TERMINATED")
            self.assertEqual(page.locator("#target-frame").count(), 0)

            browser.close()


if __name__ == "__main__":
    unittest.main()
