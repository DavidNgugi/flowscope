import unittest

from app.llm.usage import estimate_cost_usd, pricing_for_model, summarize_usage_rows


class AIUsageTests(unittest.TestCase):
    def test_sonnet_5_cost_estimate(self):
        pricing = pricing_for_model("claude-sonnet-5")
        self.assertIsNotNone(pricing)

        cost = estimate_cost_usd(
            pricing=pricing,
            input_tokens=1_000_000,
            output_tokens=100_000,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        self.assertAlmostEqual(cost, 3.0)

    def test_summarizes_usage_by_analysis_step(self):
        rows = [
            {
                "stage": "frame_analysis",
                "model": "claude-sonnet-5",
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "estimated_cost_usd": 0.0004,
            },
            {
                "stage": "synthesis",
                "model": "claude-sonnet-5",
                "input_tokens": 50,
                "output_tokens": 10,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "estimated_cost_usd": 0.0002,
            },
        ]

        summary = summarize_usage_rows(rows)

        self.assertEqual(summary["call_count"], 2)
        self.assertEqual(summary["input_tokens"], 150)
        self.assertEqual([stage["stage"] for stage in summary["by_stage"]], ["frame_analysis", "synthesis"])
        self.assertAlmostEqual(summary["estimated_cost_usd"], 0.0006)

    def test_unknown_model_has_no_guessed_price(self):
        self.assertIsNone(pricing_for_model("claude-future-model"))


if __name__ == "__main__":
    unittest.main()
