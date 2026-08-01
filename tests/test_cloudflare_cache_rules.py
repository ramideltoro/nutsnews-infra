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

    def test_router_variants_use_explicit_header_presence(self) -> None:
        source = RULES.read_text(encoding="utf-8")

        self.assertNotIn('headers[\\"next-router-state-tree\\"][0] ne \\"\\"', source)
        for header in ("rsc", "next-router-prefetch", "next-router-state-tree"):
            self.assertIn(f'has_key(http.request.headers, \\"{header}\\")', source)


if __name__ == "__main__":
    unittest.main()
