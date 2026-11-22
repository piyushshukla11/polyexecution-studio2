import { MilestoneEvent } from '../types/execution';

export interface BrowserSandboxRunOptions {
  sourceCode: string;
  languageId: 'react' | 'vanilla_js';
  timeoutMs?: number;
  onMilestone?: (event: MilestoneEvent) => void;
  onConsole?: (level: string, text: string) => void;
}

export const STRICT_IFRAME_CSP =
  "default-src 'none'; " +
  "script-src 'unsafe-inline' 'unsafe-eval' blob: data:; " +
  "style-src 'unsafe-inline'; " +
  "img-src data: blob:; " +
  "font-src data:; " +
  "connect-src 'none'; " +
  "media-src 'none'; " +
  "object-src 'none'; " +
  "frame-src 'none'; " +
  "base-uri 'none'; " +
  "form-action 'none';";

export function generateIframeSrcDoc(sourceCode: string, languageId: 'react' | 'vanilla_js'): string {
  const escapedCode = JSON.stringify(sourceCode);

  const harness =
    languageId === 'react'
      ? `
        try {
          const userCode = ${escapedCode};
          const rootEl = document.getElementById("root");
          const moduleExports = {};
          const moduleScope = { exports: moduleExports };
          const fn = new Function("exports", "module", "root", "document", "window", userCode);
          fn(moduleExports, moduleScope, rootEl, document, window);

          let Comp = moduleScope.exports.default || moduleScope.exports.App || moduleScope.exports;
          if (typeof window.React !== "undefined" && typeof window.ReactDOM !== "undefined") {
            const root = window.ReactDOM.createRoot(rootEl);
            if (typeof Comp === "function") {
              root.render(window.React.createElement(Comp));
            } else if (window.React.isValidElement(Comp)) {
              root.render(Comp);
            }
          }
          const duration = Math.round(performance.now() - startTime);
          safePostMessage("METRICS", { wall_ms: duration, dom_nodes: rootEl ? rootEl.getElementsByTagName("*").length : 0 });
          safePostMessage("MILESTONE", { milestone: "COMPLETED", wall_ms: duration });
        } catch (err) {
          const errText = err && err.stack ? err.stack : String(err);
          console.error(errText);
          safePostMessage("ERROR", { message: String(err && err.message ? err.message : err), stack: errText });
          safePostMessage("MILESTONE", { milestone: "RUNTIME_ERROR", error: errText, wall_ms: Math.round(performance.now() - startTime) });
        }
      `
      : `
        try {
          const userCode = ${escapedCode};
          const rootEl = document.getElementById("root");
          const fn = new Function("root", "document", "window", userCode);
          fn.call(window, rootEl, document, window);

          const duration = Math.round(performance.now() - startTime);
          safePostMessage("METRICS", { wall_ms: duration, dom_nodes: rootEl ? rootEl.getElementsByTagName("*").length : 0 });
          safePostMessage("MILESTONE", { milestone: "COMPLETED", wall_ms: duration });
        } catch (err) {
          const errText = err && err.stack ? err.stack : String(err);
          console.error(errText);
          safePostMessage("ERROR", { message: String(err && err.message ? err.message : err), stack: errText });
          safePostMessage("MILESTONE", { milestone: "RUNTIME_ERROR", error: errText, wall_ms: Math.round(performance.now() - startTime) });
        }
      `;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="${STRICT_IFRAME_CSP}">
  <title>PES2 Sandbox</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    html, body {
      margin: 0;
      padding: 16px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #ffffff;
      color: #0f172a;
    }
  </style>
</head>
<body>
  <div id="root"></div>
  <script>
    (function() {
      const startTime = performance.now();
      function safePostMessage(type, data) {
        try {
          if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: type, payload: data, timestamp_ms: Date.now() }, "*");
          }
        } catch (e) {}
      }
      ["log", "info", "warn", "error"].forEach(function(lvl) {
        const orig = console[lvl];
        console[lvl] = function() {
          const args = Array.prototype.slice.call(arguments).map(String);
          orig.apply(console, arguments);
          safePostMessage("CONSOLE", { level: lvl, text: args.join(" ") });
        };
      });
      window.onerror = function(msg, src, line, col, err) {
        const txt = err && err.stack ? err.stack : String(msg);
        safePostMessage("ERROR", { message: String(msg), stack: txt });
        safePostMessage("MILESTONE", { milestone: "RUNTIME_ERROR", error: txt, wall_ms: Math.round(performance.now() - startTime) });
      };
      safePostMessage("READY", { language: "${languageId}" });
      safePostMessage("MILESTONE", { milestone: "RUNNING" });
      ${harness}
    })();
  </script>
</body>
</html>`;
}
