"""Tests for client-side isolated browser sandbox architecture and security policies."""

import json
import unittest

from engine.browser_sandbox import (
    BrowserSandboxPolicy,
    MANDATORY_SANDBOX_ATTRIBUTES,
    PROHIBITED_SANDBOX_ATTRIBUTES,
    STRICT_BROWSER_CSP,
    generate_browser_sandbox_html,
    validate_sandbox_policy,
)
from engine.models import LanguageId, Milestone


class TestBrowserSandboxSecurity(unittest.TestCase):
    def test_default_policy_is_valid(self) -> None:
        policy = BrowserSandboxPolicy()
        is_valid, violations = policy.validate()
        self.assertTrue(is_valid)
        self.assertEqual(len(violations), 0)

    def test_allow_same_origin_strictly_prohibited(self) -> None:
        """allow-same-origin grants access to host cookies, localStorage, and IndexedDB, breaching isolation."""
        is_valid, violations = validate_sandbox_policy(["allow-scripts", "allow-same-origin"])
        self.assertFalse(is_valid)
        self.assertTrue(any("allow-same-origin" in v for v in violations))

    def test_top_navigation_and_popups_strictly_prohibited(self) -> None:
        """Top navigation and popups break containment and hijack host tabs."""
        for dangerous_attr in [
            "allow-top-navigation",
            "allow-top-navigation-by-user-activation",
            "allow-popups",
            "allow-popups-to-escape-sandbox",
            "allow-modals",
            "allow-forms",
            "allow-pointer-lock",
            "allow-downloads",
        ]:
            is_valid, violations = validate_sandbox_policy(["allow-scripts", dangerous_attr])
            self.assertFalse(is_valid, f"{dangerous_attr} must be strictly rejected")
            self.assertTrue(any(dangerous_attr in v for v in violations))

    def test_missing_allow_scripts_fails_validation(self) -> None:
        """Without allow-scripts, no client-side evaluation is possible."""
        is_valid, violations = validate_sandbox_policy([])
        self.assertFalse(is_valid)
        self.assertTrue(any("allow-scripts" in v for v in violations))

    def test_csp_blocks_network_exfiltration(self) -> None:
        """Content-Security-Policy must enforce connect-src 'none' to prevent exfiltration."""
        # Valid strict CSP
        policy = BrowserSandboxPolicy(
            sandbox_attributes=["allow-scripts"],
            content_security_policy=STRICT_BROWSER_CSP,
        )
        self.assertTrue(policy.validate()[0])

        # Dangerous CSP allowing outbound HTTP/HTTPS
        bad_csp = "default-src 'none'; script-src 'unsafe-inline'; connect-src https:;"
        bad_policy = BrowserSandboxPolicy(
            sandbox_attributes=["allow-scripts"],
            content_security_policy=bad_csp,
        )
        is_valid, violations = bad_policy.validate()
        self.assertFalse(is_valid)
        self.assertTrue(any("https:" in v for v in violations))

        # Dangerous CSP with wildcard
        wildcard_csp = "default-src 'none'; script-src 'unsafe-inline'; connect-src *;"
        wildcard_policy = BrowserSandboxPolicy(
            sandbox_attributes=["allow-scripts"],
            content_security_policy=wildcard_csp,
        )
        is_valid, violations = wildcard_policy.validate()
        self.assertFalse(is_valid)
        self.assertTrue(any("*" in v for v in violations))

    def test_generate_vanilla_js_sandbox_html_structure(self) -> None:
        code = "const el = document.createElement('h1'); el.textContent = 'PES2'; root.appendChild(el);"
        html = generate_browser_sandbox_html(code, LanguageId.VANILLA_JS)

        # 1. Verify strict CSP meta tag
        self.assertIn('<meta http-equiv="Content-Security-Policy"', html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn("default-src 'none'", html)

        # 2. Verify root container
        self.assertIn('<div id="root"></div>', html)

        # 3. Verify console proxy
        self.assertIn('safePostMessage("CONSOLE"', html)

        # 4. Verify global error handlers
        self.assertIn("window.onerror", html)
        self.assertIn("unhandledrejection", html)

        # 5. Verify milestone emissions
        self.assertIn(f'milestone: "{Milestone.COMPLETED.value}"', html)
        self.assertIn(f'milestone: "{Milestone.RUNTIME_ERROR.value}"', html)

        # 6. Verify source code embedding
        self.assertIn("document.createElement", html)

    def test_generate_react_sandbox_html_structure(self) -> None:
        code = "export default function App() { return <h1>Hello React</h1>; }"
        html = generate_browser_sandbox_html(code, LanguageId.REACT, react_version="18.2.0")

        # 1. Verify strict CSP
        self.assertIn("connect-src 'none'", html)

        # 2. Verify React mount harness
        self.assertIn("window.ReactDOM.createRoot", html)
        self.assertIn("window.React.createElement", html)
        self.assertIn('react_version: "18.2.0"', html)

        # 3. Verify root container and milestones
        self.assertIn('<div id="root"></div>', html)
        self.assertIn(f'milestone: "{Milestone.RUNNING.value}"', html)
        self.assertIn(f'milestone: "{Milestone.COMPLETED.value}"', html)

    def test_cookie_and_storage_host_protection_doctrine(self) -> None:
        """Verify the architectural guarantee: omitting allow-same-origin assigns an opaque null origin.

        Under HTML5 specification:
        - Origin is 'null'.
        - Access to window.parent.document throws DOMException (SecurityError).
        - Access to document.cookie is blocked.
        - Access to localStorage and sessionStorage is blocked.
        """
        policy = BrowserSandboxPolicy()
        self.assertNotIn("allow-same-origin", policy.sandbox_attributes)
        self.assertEqual(policy.sandbox_attributes, ["allow-scripts"])


if __name__ == "__main__":
    unittest.main()
