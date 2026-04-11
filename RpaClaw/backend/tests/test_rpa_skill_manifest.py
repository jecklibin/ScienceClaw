import importlib.util
import unittest
from pathlib import Path


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "rpa" / "skill_manifest.py"


class SkillManifestTests(unittest.TestCase):
    def test_build_manifest_includes_script_and_agent_steps(self):
        spec = importlib.util.spec_from_file_location("skill_manifest_module", MANIFEST_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        manifest_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manifest_module)

        steps = [
            {"type": "script", "action": "click", "target": "#search"},
            {"type": "agent", "action": "extract_text", "result_key": "title"},
        ]
        params = {
            "query": {"type": "string", "description": "Search term", "required": True}
        }

        manifest = manifest_module.build_manifest(
            skill_name="search_skill",
            description="Search and extract title",
            params=params,
            steps=steps,
        )

        self.assertEqual(manifest["version"], 2)
        self.assertEqual(manifest["name"], "search_skill")
        self.assertEqual(manifest["description"], "Search and extract title")
        self.assertEqual(manifest["goal"], "Search and extract title")
        self.assertEqual(manifest["params"], params)
        self.assertEqual([step["type"] for step in manifest["steps"]], ["script", "agent"])


if __name__ == "__main__":
    unittest.main()
