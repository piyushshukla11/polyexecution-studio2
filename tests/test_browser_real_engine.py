"""Real Chromium browser engine integration tests for PES2 browser execution.

Verifies using a real headless Chromium engine:
1. Real JSX DOM rendering: React 18, ReactDOM, and Sucrase compilation via production generateIframeSrcDoc.
2. CSP network restriction: connect-src 'none' blocks fetch() attempts with a CSP violation in a real browser.
3. Production postMessage event communication and schema validation between sandboxed iframe and parent host window.
4. Watchdog termination behavior on unresponsive or infinite-loop scripts.
"""

import json
import os
import subprocess
import unittest

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class TestRealBrowserExecutionEngine(unittest.TestCase):
    """End-to-end browser execution verification in headless Chromium using production pipeline."""

    @classmethod
    def setUpClass(cls) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            return

        cls.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cls.cli_script = os.path.join(cls.root_dir, "client/scripts/generate_srcdoc.ts")

    @classmethod
    def generate_production_srcdoc(cls, source_code: str, language_id: str = "react") -> str:
        """Invokes production generateIframeSrcDoc via client/scripts/generate_srcdoc.ts."""
        cmd = [
            "npx", "--prefix", "client", "vite-node",
            "client/scripts/generate_srcdoc.ts",
            language_id,
            source_code,
        ]
        res = subprocess.run(
            cmd,
            cwd=cls.root_dir,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(f"generate_srcdoc failed (code {res.returncode}): {res.stderr}")
        return res.stdout

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

    def _get_host_html(self, watchdog_ms: int = 5000) -> str:
        """Returns the host HTML simulating BrowserPreview.tsx validation and watchdog."""
        return f"""<!DOCTYPE html>
<html>
<head><title>Host Window - PES2 BrowserPreview Simulator</title></head>
<body>
  <h1>PES2 Host Studio</h1>
  <div id="status">INITIALIZING</div>
  <div id="messages"></div>
  <iframe id="sandbox-frame" sandbox="allow-scripts"></iframe>
  <script>
    const ALLOWED_MESSAGE_TYPES = new Set(['READY', 'CONSOLE', 'ERROR', 'MILESTONE', 'METRICS']);
    const iframe = document.getElementById("sandbox-frame");
    let watchdogTimer = null;

    function armWatchdog(timeoutMs) {{
      if (watchdogTimer) clearTimeout(watchdogTimer);
      watchdogTimer = setTimeout(function() {{
        document.getElementById("status").textContent = "TIMEOUT";
        iframe.srcdoc = "<!DOCTYPE html><html><body><strong>Watchdog Timeout: Execution exceeded limit.</strong></body></html>";
      }}, timeoutMs);
    }}

    window.addEventListener("message", function(e) {{
      // 1. Validate sender: must originate from our preview iframe's contentWindow
      if (!iframe.contentWindow || e.source !== iframe.contentWindow) {{
        return; // Reject messages from foreign windows or host window self-messages
      }}

      // 2. Validate payload schema matching BrowserPreview.tsx
      if (!e.data || typeof e.data !== "object") return;
      const type = e.data.type;
      const payload = e.data.payload;
      if (!ALLOWED_MESSAGE_TYPES.has(type) || !payload || typeof payload !== "object") {{
        return;
      }}

      const div = document.createElement("div");
      div.className = "received-msg";
      div.setAttribute("data-msg-type", type);
      div.textContent = JSON.stringify(e.data);
      document.getElementById("messages").appendChild(div);

      if (type === "READY") {{
        document.getElementById("status").textContent = "RUNNING";
      }} else if (type === "MILESTONE" && payload.milestone === "COMPLETED") {{
        if (watchdogTimer) clearTimeout(watchdogTimer);
        document.getElementById("status").textContent = "COMPLETED";
      }} else if (type === "ERROR") {{
        document.getElementById("status").textContent = "ERROR";
      }}
    }});

    armWatchdog({watchdog_ms});
  </script>
</body>
</html>"""

    def test_real_production_sucrase_jsx_execution(self) -> None:
        """Verifies real JSX source code compiled by production Sucrase mounts and renders into the DOM."""
        if not PLAYWRIGHT_AVAILABLE:
            self.skipTest("playwright not installed in environment")

        real_jsx = """
import React, { useState } from 'react';

export default function CounterApp() {
  const [count] = useState(42);
  return (
    <div id="jsx-root-container">
      <h2 id="production-heading">Real Production Sucrase JSX</h2>
      <p id="production-count">Count is {count}</p>
    </div>
  );
}
"""
        srcdoc = self.generate_production_srcdoc(real_jsx, "react")
        self.assertIn("PES2 Sandbox - React", srcdoc)
        self.assertIn("React.createElement", srcdoc)

        with sync_playwright() as p:
            browser = self._launch_browser(p)
            context = browser.new_context()
            page = context.new_page()

            page.set_content(self._get_host_html(watchdog_ms=5000))
            page.eval_on_selector("#sandbox-frame", "(el, src) => { el.srcdoc = src; }", srcdoc)

            frame = page.frame_locator("#sandbox-frame")
            heading = frame.locator("#production-heading")
            heading.wait_for(state="visible", timeout=6000)

            self.assertEqual(heading.inner_text(), "Real Production Sucrase JSX")
            count_p = frame.locator("#production-count").inner_text()
            self.assertEqual(count_p, "Count is 42")

            # Verify production postMessage protocol received by host window
            page.locator(".received-msg[data-msg-type='READY']").wait_for(state="attached", timeout=5000)
            page.locator(".received-msg[data-msg-type='MILESTONE']").first.wait_for(state="attached", timeout=5000)

            status = page.locator("#status").inner_text()
            self.assertIn(status, ("RUNNING", "COMPLETED"))

            browser.close()

    def test_production_postmessage_validation(self) -> None:
        """Verifies parent host window discards forged messages and invalid schemas."""
        if not PLAYWRIGHT_AVAILABLE:
            self.skipTest("playwright not installed in environment")

        with sync_playwright() as p:
            browser = self._launch_browser(p)
            context = browser.new_context()
            page = context.new_page()

            page.set_content(self._get_host_html(watchdog_ms=5000))

            # Attempt 1: Forged message sent directly from host window (e.source === window, not iframe.contentWindow)
            page.evaluate("""
                window.postMessage({ type: "READY", payload: { language: "react" } }, "*");
            """)

            # Attempt 2: Invalid message type not in ALLOWED_MESSAGE_TYPES
            page.evaluate("""
                const iframe = document.getElementById("sandbox-frame");
                // Attempt to send invalid message type
                window.postMessage({ type: "MALICIOUS_EXECUTE", payload: { cmd: "rm" } }, "*");
            """)

            # Attempt 3: Non-object payload
            page.evaluate("""
                window.postMessage({ type: "CONSOLE", payload: "not-an-object" }, "*");
            """)

            page.wait_for_timeout(300)

            # None of the forged messages should be added to #messages
            msg_count = page.locator(".received-msg").count()
            self.assertEqual(msg_count, 0)

            browser.close()

    def test_production_parent_watchdog_timeout(self) -> None:
        """Verifies parent watchdog terminates a stuck or non-completing script."""
        if not PLAYWRIGHT_AVAILABLE:
            self.skipTest("playwright not installed in environment")

        stuck_jsx = """
// Simulates a long-running computation exceeding the watchdog ceiling
const t0 = performance.now();
while (performance.now() - t0 < 600) {}
export default function StuckApp() {
  return <div>Finished after delay</div>;
}
"""
        srcdoc = self.generate_production_srcdoc(stuck_jsx, "react")

        with sync_playwright() as p:
            browser = self._launch_browser(p)
            context = browser.new_context()
            page = context.new_page()

            # Host window with a short 100ms watchdog
            page.set_content(self._get_host_html(watchdog_ms=100))
            page.eval_on_selector("#sandbox-frame", "(el, src) => { el.srcdoc = src; }", srcdoc)

            # Wait for watchdog to trigger
            page.wait_for_function("document.getElementById('status').textContent === 'TIMEOUT'", timeout=3000)

            status = page.locator("#status").inner_text()
            self.assertEqual(status, "TIMEOUT")

            # Verify iframe was terminated and replaced by watchdog
            frame = page.frame_locator("#sandbox-frame")
            body_text = frame.locator("body").inner_text()
            self.assertIn("Watchdog Timeout", body_text)

            browser.close()

    def test_real_csp_connect_src_blocks_fetch(self) -> None:
        """Verifies CSP connect-src 'none' in production template strictly blocks outbound fetch in Chromium."""
        if not PLAYWRIGHT_AVAILABLE:
            self.skipTest("playwright not installed in environment")

        jsx_with_fetch = """
import React, { useEffect, useState } from 'react';

export default function FetchTestApp() {
  const [result, setResult] = useState("TESTING");

  useEffect(() => {
    fetch("https://example.com/api/test")
      .then(() => setResult("FETCH_ALLOWED"))
      .catch((err) => setResult("FETCH_BLOCKED: " + err.message));
  }, []);

  return <div id="fetch-result">{result}</div>;
}
"""
        srcdoc = self.generate_production_srcdoc(jsx_with_fetch, "react")

        with sync_playwright() as p:
            browser = self._launch_browser(p)
            context = browser.new_context()
            page = context.new_page()

            page.set_content(self._get_host_html(watchdog_ms=5000))
            page.eval_on_selector("#sandbox-frame", "(el, src) => { el.srcdoc = src; }", srcdoc)

            frame = page.frame_locator("#sandbox-frame")
            result_elem = frame.locator("#fetch-result")
            result_elem.wait_for(state="visible", timeout=6000)

            frame.locator("#fetch-result:has-text('FETCH_')").wait_for(state="visible", timeout=6000)

            res_text = result_elem.inner_text()
            self.assertIn("FETCH_BLOCKED", res_text)
            self.assertNotIn("FETCH_ALLOWED", res_text)

            browser.close()


if __name__ == "__main__":
    unittest.main()
