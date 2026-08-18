import unittest
from pathlib import Path

from rag_assistant.ui_options import context_size_options, normalized_context_limit


class UiOptionsTests(unittest.TestCase):
    def test_single_context_option_is_valid_for_8k_model(self):
        self.assertEqual([8192], context_size_options(8192))

    def test_missing_or_invalid_limit_falls_back_to_8k(self):
        self.assertEqual([8192], context_size_options(None))
        self.assertEqual([8192], context_size_options(0))
        self.assertEqual([8192], context_size_options("unknown"))
        self.assertEqual(8192, normalized_context_limit("unknown"))

    def test_options_do_not_exceed_model_limit(self):
        self.assertEqual([8192, 16384, 32768], context_size_options(49152))

    def test_app_does_not_render_a_zero_range_context_slider(self):
        app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn("if len(context_options) == 1:", app)
        self.assertIn('num_ctx = st.selectbox(', app)


if __name__ == "__main__":
    unittest.main()
