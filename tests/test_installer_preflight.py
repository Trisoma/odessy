"""Installer preflight tests: no third-party packages or real services required."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.preflight import (
    Location, build_report, default_locations, inspect_location, main, render,
    repository_defaults,
)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "odessy.env").write_text(
            '# defaults\nODESSY_MODEL_NAME="fixture-model:1b"\n'
            'ODESSY_MODEL_ENDPOINT=http://127.0.0.1:11434\n'
            'OPENAI_API_KEY=must-not-appear\n', encoding="utf-8")

    def report(self, **kwargs):
        options = dict(env={}, home=self.root, system="Windows", machine="AMD64",
                       repo_root=self.root)
        options.update(kwargs)
        return build_report(self.root / "app", self.root / "models", **options)

    def test_clean_machine_without_dependencies_can_preview(self):
        report = self.report()
        self.assertTrue(report["preflight_passed"])
        self.assertFalse(report["installation_ready"])
        self.assertFalse((self.root / "app").exists())
        self.assertFalse((self.root / "models").exists())
        self.assertFalse((self.root / ".config").exists())
        self.assertNotIn("must-not-appear", json.dumps(report))
        self.assertEqual({p.name for p in self.root.iterdir()}, {"config"})
        self.assertIn("not ready", render(report))

    def test_unsupported_platforms_and_arm_are_not_accepted(self):
        for system, machine in (("Linux", "x86_64"), ("Windows", "ARM64"), ("Windows", "x86")):
            with self.subTest(system=system, machine=machine):
                self.assertFalse(self.report(system=system, machine=machine)["preflight_passed"])

    def test_unwritable_directory_blocks_preflight(self):
        report = self.report(inspect=lambda p: Location(str(p), None, None, False, "Access denied"))
        self.assertFalse(report["preflight_passed"])
        self.assertIn("Access denied", render(report))

    def test_file_in_path_is_rejected_without_modification(self):
        target = self.root / "file"
        target.write_text("preserve", encoding="utf-8")
        self.assertFalse(inspect_location(target / "child").writable)
        self.assertEqual(target.read_text(encoding="utf-8"), "preserve")

    def test_relative_target_is_rejected(self):
        self.assertFalse(inspect_location(Path("relative-app")).writable)

    def test_paths_with_spaces_and_unicode(self):
        parent = self.root / "安装 space"
        parent.mkdir()
        self.assertTrue(inspect_location(parent / "Odessy").writable)
        self.assertEqual(list(parent.iterdir()), [])

    def test_existing_configuration_is_not_read_or_changed(self):
        config = self.root / "personal.env"
        original = b"OPENAI_API_KEY=private-value\r\nODESSY_MODEL_NAME=my-model\r\n"
        config.write_bytes(original)
        report = self.report(env={"ODESSY_CONFIG_FILE": str(config)})
        self.assertEqual(report["configuration"]["path"], str(config))
        self.assertTrue(report["configuration"]["exists"])
        self.assertEqual(config.read_bytes(), original)
        self.assertNotIn("private-value", json.dumps(report))

    def test_missing_or_malformed_defaults_block_preflight(self):
        config = self.root / "config" / "odessy.env"
        config.write_text("# no model defaults\n", encoding="utf-8")
        self.assertFalse(self.report()["preflight_passed"])
        config.unlink()
        self.assertFalse(self.report()["preflight_passed"])

    def test_model_default_is_taken_from_project_file(self):
        self.assertEqual(repository_defaults(self.root / "config" / "odessy.env")["ODESSY_MODEL_NAME"],
                         "fixture-model:1b")

    def test_duplicate_defaults_match_existing_config_loader_last_value_wins(self):
        config = self.root / "config" / "odessy.env"
        with config.open("a", encoding="utf-8") as stream:
            stream.write("ODESSY_MODEL_NAME=replacement-model\n")
        self.assertEqual(repository_defaults(config)["ODESSY_MODEL_NAME"], "replacement-model")

    def test_empty_model_default_is_a_blocker(self):
        config = self.root / "config" / "odessy.env"
        config.write_text("ODESSY_MODEL_NAME=\nODESSY_MODEL_ENDPOINT=http://localhost:11434\n",
                          encoding="utf-8")
        self.assertFalse(self.report()["preflight_passed"])

    def test_disk_budget_is_pending_even_when_directories_are_writable(self):
        report = self.report(inspect=lambda p: Location(str(p), str(self.root), 100, True))
        self.assertEqual(next(c for c in report["checks"] if c["name"] == "disk_budget")["status"], "pending")
        self.assertFalse(report["installation_ready"])

    def test_default_locations_use_per_user_local_appdata(self):
        app, models = default_locations({"LOCALAPPDATA": str(self.root)}, self.root)
        self.assertEqual(app, self.root / "Programs" / "Odessy")
        self.assertEqual(models, self.root / "Odessy" / "models")

    def test_selected_manifest_is_validated_without_downloading(self):
        manifest = self.root / "downloads.json"
        manifest.write_text(json.dumps({"schema_version": 1, "release": "1.0.0",
            "platform": "windows-x64", "artifacts": [{"id": "fixture", "version": "1.0.0",
            "url": "https://example.test/v1/file", "size_bytes": 10, "sha256": "a" * 64}]}), encoding="utf-8")
        report = self.report(manifest_path=manifest)
        self.assertTrue(report["preflight_passed"])
        self.assertEqual(report["download_manifest"]["download_bytes"], 10)
        self.assertFalse(report["installation_ready"])
        self.assertEqual({p.name for p in self.root.iterdir()}, {"config", "downloads.json"})

    def test_invalid_selected_manifest_blocks_preview(self):
        report = self.report(manifest_path=self.root / "missing.json")
        self.assertFalse(report["preflight_passed"])
        self.assertIsNone(report["download_manifest"])

    def test_json_cli_has_no_side_effects_and_returns_failure_for_blockers(self):
        with patch("installer.preflight.build_report", return_value={"preflight_passed": False}), \
             patch("builtins.print") as output:
            self.assertEqual(main(["--json"]), 1)
        self.assertEqual(json.loads(output.call_args.args[0]), {"preflight_passed": False})


if __name__ == "__main__":
    unittest.main()
