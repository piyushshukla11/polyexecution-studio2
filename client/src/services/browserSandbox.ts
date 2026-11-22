/**
 * Browser Navigation Boundary and Sandbox Security Limitations:
 *
 * 1. Host DOM, cookie, and storage protection:
 *    The iframe uses sandbox="allow-scripts" without "allow-same-origin".
 *    This assigns the iframe an opaque ("null") origin, preventing access to the host
 *    DOM, localStorage, sessionStorage, IndexedDB, and sensitive document cookies.
 *
 * 2. Outbound network exfiltration prevention:
 *    Content-Security-Policy (connect-src 'none') strictly blocks outbound network
 *    calls made via fetch, XMLHttpRequest, WebSocket, EventSource, or sendBeacon.
 *    Disallowing allow-forms prevents automated credential phishing.
 *
 * 3. Browser self-navigation limitation:
 *    Omitting "allow-top-navigation" prevents the sandboxed iframe from redirecting the
 *    top-level host studio window. However, internal self-navigation within the iframe itself
 *    (e.g., window.location.href = 'about:blank' or clicking an anchor tag targeting _self)
 *    cannot be reliably prevented by HTML5 iframe sandbox attributes alone in all browser engines.
 *    Even if an internal redirect is attempted, connect-src 'none' and frame-src 'none'
 *    prevent loading external resources, and the parent-side watchdog terminates execution.
 */

import { transform } from 'sucrase';
import reactUmd from '../assets/vendor/react.production.min.js?raw';
import reactDomUmd from '../assets/vendor/react-dom.production.min.js?raw';
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

export function transpileReactSource(sourceCode: string): { code: string; error: string | null } {
  try {
    const result = transform(sourceCode, {
      transforms: ['jsx', 'imports'],
      production: true,
    });
    return { code: result.code, error: null };
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { code: '', error: msg };
  }
}

export function generateIframeSrcDoc(sourceCode: string, languageId: 'react' | 'vanilla_js'): string {
  if (languageId === 'react') {
    const { code: transpiledCode, error: transpileError } = transpileReactSource(sourceCode);

    if (transpileError) {
      const escapedErr = JSON.stringify(transpileError);
      return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="${STRICT_IFRAME_CSP}">
  <title>PES2 Sandbox - JSX Transpile Error</title>
</head>
<body>
  <div id="root" style="color: #ef4444; font-family: monospace; padding: 16px;"></div>
  <script>
    (function() {
      const err = ${escapedErr};
      document.getElementById("root").textContent = "JSX Compilation Error: " + err;
      try {
        if (window.parent && window.parent !== window) {
          window.parent.postMessage({
            type: "ERROR",
            payload: { message: err, stack: err },
            timestamp_ms: Date.now()
          }, "*");
          window.parent.postMessage({
            type: "MILESTONE",
            payload: { milestone: "RUNTIME_ERROR", error: err, wall_ms: 0 },
            timestamp_ms: Date.now()
          }, "*");
        }
      } catch (e) {}
    })();
  </script>
</body>
</html>`;
    }

    const escapedTranspiled = JSON.stringify(transpiledCode);

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="${STRICT_IFRAME_CSP}">
  <title>PES2 Sandbox - React</title>
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
  <script>${reactUmd}</script>
  <script>${reactDomUmd}</script>
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

      safePostMessage("READY", { language: "react" });
      safePostMessage("MILESTONE", { milestone: "RUNNING" });

      try {
        const _moduleRegistry = {
          'react': window.React,
          'react-dom': window.ReactDOM,
          'react-dom/client': window.ReactDOM,
        };
        function require(name) {
          if (_moduleRegistry[name]) {
            return _moduleRegistry[name];
          }
          throw new Error("Module '" + name + "' is not available in isolated sandbox. Supported: 'react', 'react-dom'");
        }

        const exports = {};
        const module = { exports: exports };
        const code = ${escapedTranspiled};

        const fn = new Function("require", "exports", "module", "React", "ReactDOM", code);
        fn(require, exports, module, window.React, window.ReactDOM);

        let Comp = module.exports.default || module.exports.App || module.exports;
        if (typeof Comp === "object" && Comp !== null && !window.React.isValidElement(Comp)) {
          for (const k of Object.keys(Comp)) {
            if (typeof Comp[k] === "function") {
              Comp = Comp[k];
              break;
            }
          }
        }

        const rootEl = document.getElementById("root");
        if (window.ReactDOM && rootEl) {
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
    })();
  </script>
</body>
</html>`;
  }

  const escapedCode = JSON.stringify(sourceCode);
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="${STRICT_IFRAME_CSP}">
  <title>PES2 Sandbox - Vanilla JS</title>
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

      safePostMessage("READY", { language: "vanilla_js" });
      safePostMessage("MILESTONE", { milestone: "RUNNING" });

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
    })();
  </script>
</body>
</html>`;
}
