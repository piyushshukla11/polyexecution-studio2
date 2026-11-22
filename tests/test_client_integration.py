"""Integration and consistency tests between frontend client and backend engine."""

import json
import os
import unittest

from engine.models import LanguageId
from engine.registry import RuntimeRegistry


class TestClientBackendIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.client_dir = os.path.join(self.project_root, "client")

    def test_client_configuration_files_exist(self) -> None:
        self.assertTrue(os.path.exists(os.path.join(self.client_dir, "package.json")))
        self.assertTrue(os.path.exists(os.path.join(self.client_dir, "vite.config.ts")))
        self.assertTrue(os.path.exists(os.path.join(self.client_dir, "tsconfig.json")))
        self.assertTrue(os.path.exists(os.path.join(self.client_dir, "index.html")))

    def test_all_backend_languages_represented_in_frontend(self) -> None:
        lang_file = os.path.join(self.client_dir, "src", "constants", "languages.ts")
        self.assertTrue(os.path.exists(lang_file))

        with open(lang_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Every LanguageId enum value must be present in the frontend languages definition
        backend_langs = [l.value for l in LanguageId]
        self.assertEqual(len(backend_langs), 28)

        for lang_id in backend_langs:
            self.assertIn(
                f"id: '{lang_id}'",
                content,
                f"Language '{lang_id}' from engine.models.LanguageId missing in frontend languages.ts",
            )

    def test_all_languages_have_starter_templates(self) -> None:
        template_file = os.path.join(self.client_dir, "src", "constants", "templates.ts")
        self.assertTrue(os.path.exists(template_file))

        with open(template_file, "r", encoding="utf-8") as f:
            content = f.read()

        for lang_id in [l.value for l in LanguageId]:
            self.assertIn(
                f"{lang_id}:",
                content,
                f"Template for '{lang_id}' missing in frontend templates.ts",
            )

    def test_client_build_artifacts_present(self) -> None:
        dist_html = os.path.join(self.client_dir, "dist", "index.html")
        self.assertTrue(
            os.path.exists(dist_html),
            "Client production bundle dist/index.html not found. Run 'npm run build' in client/",
        )

    def test_collaboration_honesty_notice_present(self) -> None:
        collab_file = os.path.join(self.client_dir, "src", "hooks", "useCollaboration.ts")
        self.assertTrue(os.path.exists(collab_file))

        with open(collab_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Must explicitly document WebSocket CRDT document gateway dependency
        self.assertIn("WebSocket CRDT document gateway", content)
        self.assertIn("standalone", content)


if __name__ == "__main__":
    unittest.main()
