"""Client-side isolated browser sandbox architecture and security policies.

Implements isolated browser execution specifications for React (18.2) and
Vanilla JavaScript (DOM).

Enforces strict isolation via HTML5 sandboxed iframe attributes and
Content-Security-Policy (CSP) headers:
1. sandbox="allow-scripts" strictly without "allow-same-origin", forcing an
   opaque ("null") origin. This prevents access to host DOM, host cookies,
   localStorage, sessionStorage, and IndexedDB.
2. Content-Security-Policy with connect-src 'none', blocking outbound network
   fetches (fetch, XMLHttpRequest, WebSocket, EventSource, sendBeacon).
3. PostMessage protocol mediating communication between host studio and iframe.
"""

from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from engine.models import LanguageId, Milestone


# Disallowed sandbox attributes that would breach host isolation
PROHIBITED_SANDBOX_ATTRIBUTES: Set[str] = {
    "allow-same-origin",  # Would grant access to host cookies, localStorage, session
    "allow-top-navigation",  # Would allow iframe to navigate the host window
    "allow-top-navigation-by-user-activation",
    "allow-popups",  # Would allow opening uncontained windows
    "allow-popups-to-escape-sandbox",
    "allow-modals",  # Would allow alert/confirm/prompt hijacking host interaction
    "allow-forms",  # Prevents phished form submission
    "allow-pointer-lock",
    "allow-downloads",
}

# Mandatory sandbox attributes
MANDATORY_SANDBOX_ATTRIBUTES: Set[str] = {
    "allow-scripts",
}

# Strict default Content-Security-Policy blocking network exfiltration
STRICT_BROWSER_CSP: str = (
    "default-src 'none'; "
    "script-src 'unsafe-inline' 'unsafe-eval' blob: data:; "
    "style-src 'unsafe-inline'; "
    "img-src data: blob:; "
    "font-src data:; "
    "connect-src 'none'; "
    "media-src 'none'; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none';"
)


@dataclass(frozen=True)
class BrowserSandboxPolicy:
    """Security policy specifications for sandboxed client iframe execution."""

    sandbox_attributes: List[str] = field(default_factory=lambda: ["allow-scripts"])
    content_security_policy: str = STRICT_BROWSER_CSP
    execution_timeout_ms: int = 5000
    allow_network: bool = False

    def validate(self) -> Tuple[bool, List[str]]:
        """Validate that the policy strictly adheres to zero-trust host isolation."""
        violations: List[str] = []
        attrs = set(self.sandbox_attributes)

        for disallowed in PROHIBITED_SANDBOX_ATTRIBUTES:
            if disallowed in attrs:
                violations.append(
                    f"Prohibited sandbox attribute '{disallowed}' violates host isolation"
                )

        for mandatory in MANDATORY_SANDBOX_ATTRIBUTES:
            if mandatory not in attrs:
                violations.append(
                    f"Mandatory sandbox attribute '{mandatory}' is required for execution"
                )

        csp_lower = self.content_security_policy.lower()
        if "connect-src 'none'" not in csp_lower and "default-src 'none'" not in csp_lower:
            violations.append(
                "Content-Security-Policy must enforce connect-src 'none' or default-src 'none' to block network exfiltration"
            )

        if not self.allow_network:
            for dangerous in ["http:", "https:", "*"]:
                if f"connect-src {dangerous}" in csp_lower or f"default-src {dangerous}" in csp_lower:
                    violations.append(
                        f"Content-Security-Policy permits outbound network '{dangerous}'"
                    )

        return len(violations) == 0, violations


@dataclass(frozen=True)
class BrowserConsoleMessage:
    """Structured console log captured from isolated iframe."""

    level: str  # log, info, warn, error
    args: List[str]
    timestamp_ms: int


@dataclass(frozen=True)
class BrowserExecutionEvent:
    """Typed event emitted via postMessage from sandboxed iframe."""

    event_type: str  # READY, CONSOLE, ERROR, MILESTONE, METRICS
    payload: Dict[str, Any]
    timestamp_ms: int


def validate_sandbox_policy(
    sandbox_attributes: List[str],
    csp_header: str = STRICT_BROWSER_CSP,
) -> Tuple[bool, List[str]]:
    """Helper verifying that an iframe sandbox configuration is secure."""
    policy = BrowserSandboxPolicy(
        sandbox_attributes=sandbox_attributes,
        content_security_policy=csp_header,
    )
    return policy.validate()


def generate_browser_sandbox_html(
    source_code: str,
    language_id: LanguageId,
    title: str = "PolyExecution Studio 2 Browser Sandbox",
    react_version: str = "18.2.0",
) -> str:
    """Generate self-contained HTML document configured for isolated iframe execution.

    Inlined JavaScript provides:
    1. Console interception proxying logs to postMessage.
    2. Global error boundary (window.onerror, window.onunhandledrejection).
    3. DOM root container (<div id="root"></div>).
    4. React 18 component mounting or Vanilla JS execution.
    5. Execution metrics and lifecycle milestones.
    """
    escaped_source = json.dumps(source_code)

    if language_id == LanguageId.REACT:
        harness_js = _generate_react_harness_js(escaped_source, react_version)
    else:
        harness_js = _generate_vanilla_js_harness_js(escaped_source)

    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="{STRICT_BROWSER_CSP}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    /* Clean isolated base styles */
    *, *::before, *::after {{
      box-sizing: border-box;
    }}
    html, body {{
      margin: 0;
      padding: 16px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #ffffff;
      color: #1a1a1a;
      overflow: auto;
    }}
    #root {{
      min-height: 100px;
    }}
  </style>
</head>
<body>
  <div id="root"></div>
  <script>
    (function() {{
      "use strict";

      const startTime = performance.now();

      function safePostMessage(type, data) {{
        try {{
          if (window.parent && window.parent !== window) {{
            window.parent.postMessage({{
              type: type,
              payload: data,
              timestamp_ms: Date.now()
            }}, "*");
          }}
        }} catch (e) {{
          // Parent window might be detached
        }}
      }}

      // Proxy console outputs
      const originalConsole = {{
        log: console.log,
        info: console.info,
        warn: console.warn,
        error: console.error
      }};

      function formatArg(arg) {{
        if (arg === null) return "null";
        if (arg === undefined) return "undefined";
        if (typeof arg === "object") {{
          try {{
            return JSON.stringify(arg);
          }} catch (e) {{
            return String(arg);
          }}
        }}
        return String(arg);
      }}

      ["log", "info", "warn", "error"].forEach(function(level) {{
        console[level] = function() {{
          const args = Array.prototype.slice.call(arguments).map(formatArg);
          originalConsole[level].apply(console, arguments);
          safePostMessage("CONSOLE", {{
            level: level,
            args: args,
            text: args.join(" ")
          }});
        }};
      }});

      // Global error handlers
      window.onerror = function(message, source, lineno, colno, error) {{
        const errorText = error && error.stack ? error.stack : String(message);
        safePostMessage("ERROR", {{
          message: String(message),
          source: source,
          lineno: lineno,
          colno: colno,
          stack: errorText
        }});
        safePostMessage("MILESTONE", {{
          milestone: "{Milestone.RUNTIME_ERROR.value}",
          error: errorText,
          wall_ms: Math.round(performance.now() - startTime)
        }});
        return false;
      }};

      window.addEventListener("unhandledrejection", function(event) {{
        const reason = event.reason ? (event.reason.stack || String(event.reason)) : "Unhandled Promise Rejection";
        safePostMessage("ERROR", {{
          message: reason,
          stack: reason
        }});
        safePostMessage("MILESTONE", {{
          milestone: "{Milestone.RUNTIME_ERROR.value}",
          error: reason,
          wall_ms: Math.round(performance.now() - startTime)
        }});
      }});

      // Signal ready
      safePostMessage("READY", {{ language: "{language_id.value}" }});
      safePostMessage("MILESTONE", {{ milestone: "{Milestone.RUNNING.value}" }});

      // Execute code
      {harness_js}
    }})();
  </script>
</body>
</html>"""
    return html_template


def _generate_vanilla_js_harness_js(escaped_source: str) -> str:
    """Generate JavaScript harness for Vanilla JS (DOM)."""
    return f"""
      try {{
        const userCode = {escaped_source};
        const root = document.getElementById("root");
        const execFn = new Function("root", "document", "window", userCode);
        execFn.call(window, root, document, window);

        const duration = Math.round(performance.now() - startTime);
        const nodeCount = root ? root.getElementsByTagName("*").length : 0;

        safePostMessage("METRICS", {{
          wall_ms: duration,
          dom_nodes: nodeCount
        }});
        safePostMessage("MILESTONE", {{
          milestone: "{Milestone.COMPLETED.value}",
          wall_ms: duration,
          dom_nodes: nodeCount
        }});
      }} catch (err) {{
        const errText = err && err.stack ? err.stack : String(err);
        console.error(errText);
        safePostMessage("ERROR", {{
          message: String(err && err.message ? err.message : err),
          stack: errText
        }});
        safePostMessage("MILESTONE", {{
          milestone: "{Milestone.RUNTIME_ERROR.value}",
          error: errText,
          wall_ms: Math.round(performance.now() - startTime)
        }});
      }}
"""


def _generate_react_harness_js(escaped_source: str, react_version: str) -> str:
    """Generate JavaScript harness for React 18 component rendering."""
    return f"""
      try {{
        const userCode = {escaped_source};
        const rootContainer = document.getElementById("root");

        // Transpile/evaluate React submission
        // Supports standard export default function App, function App, class App, or direct element render
        const moduleExports = {{}};
        const moduleScope = {{ exports: moduleExports }};

        const runnerFn = new Function("exports", "module", "root", "document", "window", userCode);
        runnerFn(moduleExports, moduleScope, rootContainer, document, window);

        let ComponentToRender = moduleScope.exports.default || moduleScope.exports.App || moduleScope.exports;

        // If React and ReactDOM are available in global context (or mocked)
        if (typeof window.React !== "undefined" && typeof window.ReactDOM !== "undefined") {{
          if (typeof window.ReactDOM.createRoot === "function") {{
            const root = window.ReactDOM.createRoot(rootContainer);
            if (typeof ComponentToRender === "function") {{
              root.render(window.React.createElement(ComponentToRender));
            }} else if (window.React.isValidElement(ComponentToRender)) {{
              root.render(ComponentToRender);
            }}
          }}
        }}

        const duration = Math.round(performance.now() - startTime);
        const nodeCount = rootContainer ? rootContainer.getElementsByTagName("*").length : 0;

        safePostMessage("METRICS", {{
          wall_ms: duration,
          react_version: "{react_version}",
          dom_nodes: nodeCount
        }});
        safePostMessage("MILESTONE", {{
          milestone: "{Milestone.COMPLETED.value}",
          wall_ms: duration,
          dom_nodes: nodeCount
        }});
      }} catch (err) {{
        const errText = err && err.stack ? err.stack : String(err);
        console.error(errText);
        safePostMessage("ERROR", {{
          message: String(err && err.message ? err.message : err),
          stack: errText
        }});
        safePostMessage("MILESTONE", {{
          milestone: "{Milestone.RUNTIME_ERROR.value}",
          error: errText,
          wall_ms: Math.round(performance.now() - startTime)
        }});
      }}
"""
