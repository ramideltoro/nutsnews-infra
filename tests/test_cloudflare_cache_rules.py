import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RULES = ROOT / "terraform" / "cloudflare-cache" / "main.tf"


class CloudflareCacheRuleTests(unittest.TestCase):
    def test_optimized_images_normalize_supported_accept_media_types(self) -> None:
        source = RULES.read_text(encoding="utf-8")

        self.assertNotIn('include        = ["accept"]', source)
        self.assertIn('accept = ["image/avif", "image/webp"]', source)


if __name__ == "__main__":
    unittest.main()
