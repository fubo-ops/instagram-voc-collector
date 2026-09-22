import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryDistributionTests(unittest.TestCase):
    def test_public_repository_files_exist(self):
        required = [
            "README.md", "SKILL.md", "LICENSE", "pyproject.toml",
            "requirements.txt", "package.json", "package-lock.json",
            ".github/workflows/test.yml", "agents/openai.yaml",
            "references/collection-guide.md", "references/raw-record-schema.md",
            "scripts/instagram_automation.py",
            "scripts/instagram_playwright_collector.cjs",
        ]
        missing = [name for name in required if not (ROOT / name).is_file()]
        self.assertEqual(missing, [])

    def test_generated_and_sensitive_paths_are_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for rule in ["outputs/", "node_modules/", ".venv/", "*.xlsx", "*.jsonl"]:
            self.assertIn(rule, ignored)

    def test_ci_covers_python_node_and_skill_sources(self):
        workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        for command in [
            "python -m unittest discover -s tests -v",
            "npm test",
            "npm run check",
        ]:
            self.assertIn(command, workflow)


if __name__ == "__main__":
    unittest.main()
