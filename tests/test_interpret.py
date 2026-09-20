import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from interpret import (
    Interpretation,
    InterpretationError,
    request_interpretation,
    save_interpretation,
    transcript_body,
)


class InterpretationTests(unittest.TestCase):
    def test_paid_model_is_rejected_before_network_access(self):
        with patch("interpret.urllib.request.urlopen") as send:
            with self.assertRaisesRegex(InterpretationError, "free"):
                request_interpretation("sample", "openai/gpt-4.1", "test-key")
            send.assert_not_called()

    def test_free_request_sends_only_transcript_and_saves_separate_result(self):
        source = (
            '---\napp: "Source"\naudio_sources:\n  mic: "private.wav"\n---\n\n'
            '# Transcript\n\n[12:00:01] A synthetic meeting.\n'
        )
        body = transcript_body(source)
        reply = {
            "model": "example/free-model:free",
            "choices": [{"message": {"content": "## Summary\nA synthetic meeting."}}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 9},
        }
        with patch("interpret.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(reply).encode())) as send:
            result = request_interpretation(body, "openrouter/free", "test-key")
        request = send.call_args.args[0]
        sent = json.loads(request.data)
        self.assertEqual(sent["model"], "openrouter/free")
        self.assertEqual(sent["messages"][1]["content"], body)
        self.assertNotIn("private.wav", request.data.decode())
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

        with tempfile.TemporaryDirectory() as temp_dir:
            recordings = Path(temp_dir)
            original = recordings / "sample.md"
            original.write_text(source, encoding="utf-8")
            output = save_interpretation(original, source, result, recordings)
            self.assertEqual(original.read_text(encoding="utf-8"), source)
            self.assertEqual(output.parent, recordings / "interpretations")
            self.assertIn("Model: `example/free-model:free`", output.read_text())
            self.assertIn("Source SHA-256:", output.read_text())
            second = save_interpretation(original, source, result, recordings)
            self.assertNotEqual(output, second)

    def test_auth_failure_is_clear_and_does_not_echo_response(self):
        error = urllib.error.HTTPError("https://openrouter.ai", 401, "secret", {}, None)
        with patch("interpret.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(InterpretationError, "rejected the API key") as caught:
                request_interpretation("sample", "openrouter/free", "test-key")
        self.assertNotIn("secret", str(caught.exception))

    def test_transcript_requires_body(self):
        with self.assertRaises(InterpretationError):
            transcript_body("# Transcript\n\n")

class RoutingAndHistoryTests(unittest.TestCase):
    def test_router_suffix_cannot_bypass_free_only_policy(self):
        for model in ("openrouter/auto:free", "openrouter/auto", "example/custom:free"):
            with self.subTest(model=model), patch("interpret.urllib.request.urlopen") as send:
                with self.assertRaises(InterpretationError):
                    request_interpretation("sample", model, "test-key")
                send.assert_not_called()

    def test_summary_remains_associated_after_recording_rename(self):
        from interpret import find_interpretation
        source = '---\ntitle: Old\n---\n\n# Transcript\n\n[12:00:00] Test meeting.'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = save_interpretation(root/'old.md', source, Interpretation('Summary', 'free', 1, 1), root)
            renamed = source.replace('title: Old', 'title: New')
            self.assertEqual(find_interpretation(renamed, root), result)
            self.assertIsNone(find_interpretation(source.replace('Test meeting.', 'Different meeting.'), root))


if __name__ == "__main__":
    unittest.main()
