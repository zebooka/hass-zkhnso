"""Tests for release changelog output."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class TestMakeVersion(unittest.TestCase):
    def setUp(self):
        makefile = Path(__file__).resolve().parents[3] / "Makefile"
        if not shutil.which("make") or not makefile.exists():
            self.skipTest("make or the project Makefile is unavailable")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        shutil.copyfile(makefile, self.project / "Makefile")
        self.manifest = self.project / "custom_components/zkhnso/manifest.json"
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
        self.changelog = self.project / "CHANGELOG.md"

    def test_version_prints_only_new_release(self):
        for next_section in ("\n## v1.0.0\n\n### Исправлено\n\n- Старое изменение.\n", ""):
            with self.subTest(next_section=next_section):
                self.changelog.write_text(
                    "# История изменений\n\n## В разработке\n\n### Добавлено\n\n- Новое изменение.\n" + next_section,
                    encoding="utf-8",
                )
                result = subprocess.run(
                    ["make", "version", "v1.1.0"], cwd=self.project, text=True, capture_output=True, check=True,
                )
                self.assertTrue(result.stdout.endswith("## v1.1.0\n\n### Добавлено\n\n- Новое изменение.\n"))
                self.assertNotIn("## v1.0.0", result.stdout)
                self.assertEqual(json.loads(self.manifest.read_text())["version"], "1.1.0")
                self.assertEqual(self.changelog.read_text(encoding="utf-8"), (
                    "# История изменений\n\n## v1.1.0\n\n### Добавлено\n\n- Новое изменение.\n" + next_section
                ))

    def test_missing_unreleased_section_does_not_modify_manifest(self):
        self.changelog.write_text("# История изменений\n\n## v1.0.0\n", encoding="utf-8")
        result = subprocess.run(
            ["make", "version", "1.1.0"], cwd=self.project, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.manifest.read_text())["version"], "1.0.0")
