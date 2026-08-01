import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RULES = ROOT / "terraform" / "cloudflare-cache" / "main.tf"


class CloudflareCacheRuleTests(unittest.TestCase):
    def test_plan_compatible_rules_avoid_selective_custom_cache_keys(self) -> None:
        source = RULES.read_text(encoding="utf-8")

        self.assertNotIn('include        = ["accept"]', source)
        self.assertNotIn("include = { list =", source)

    def test_optimized_images_normalize_supported_accept_media_types(self) -> None:
        source = RULES.read_text(encoding="utf-8")

        self.assertIn('media_types = ["image/avif", "image/webp"]', source)
        self.assertIn('default = { action = "bypass" }', source)


if __name__ == "__main__":
    unittest.main()
