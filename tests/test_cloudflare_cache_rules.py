import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RULES = ROOT / "terraform" / "cloudflare-cache" / "main.tf"
APPLY_WORKFLOW = ROOT / ".github" / "workflows" / "cloudflare-cache-rules-apply.yml"


class CloudflareCacheRuleTests(unittest.TestCase):
    def test_apply_workflow_preserves_non_canceling_operations(self) -> None:
        source = APPLY_WORKFLOW.read_text(encoding="utf-8")
        concurrency = source[source.index("concurrency:") : source.index("jobs:")]

        self.assertIn("group: cloudflare-cache-rules-apply", concurrency)
        self.assertIn("queue: max", concurrency)
        self.assertIn("cancel-in-progress: false", concurrency)

    def test_apply_workflow_authorizes_repository_and_main_before_environment(self) -> None:
        source = APPLY_WORKFLOW.read_text(encoding="utf-8")
        job_header = source[source.index("jobs:") : source.index("    steps:")]
        exact_guard = (
            "    if: ${{ github.repository == 'ramideltoro/nutsnews-infra' && "
            "github.ref == 'refs/heads/main' }}"
        )

        self.assertIn(exact_guard, job_header)
        self.assertLess(job_header.index(exact_guard), job_header.index("environment: cloudflare-admin"))

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
