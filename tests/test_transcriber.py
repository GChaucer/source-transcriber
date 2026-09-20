import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from transcriber import Transcriber


class TranscriberTests(unittest.TestCase):
    def test_near_silence_does_not_reach_whisper(self):
        transcriber = Transcriber()
        transcriber.model = Mock()

        result = transcriber.transcribe_chunk(np.full(16000 * 8, 0.0005, dtype=np.float32))

        self.assertEqual(result, "")
        transcriber.model.transcribe.assert_not_called()

    def test_audible_chunk_still_uses_whisper_and_glossary(self):
        transcriber = Transcriber()
        transcriber.model = Mock()
        transcriber.model.transcribe.return_value = ([SimpleNamespace(text=" Speech remains.")], None)

        result = transcriber.transcribe_chunk(
            np.full(16000 * 8, 0.02, dtype=np.float32), initial_prompt="OpenRouter"
        )

        self.assertEqual(result, "Speech remains.")
        self.assertEqual(
            transcriber.model.transcribe.call_args.kwargs["initial_prompt"], "OpenRouter"
        )

class BriefSpeechTests(unittest.TestCase):
    def test_short_quiet_signal_is_not_diluted_by_surrounding_silence(self):
        audio = np.zeros(16000 * 8, dtype=np.float32)
        audio[1000:4200] = .005 * np.sin(2 * np.pi * 200 * np.arange(3200) / 16000)
        transcriber = Transcriber()
        transcriber.model = Mock()
        transcriber.model.transcribe.return_value = ([SimpleNamespace(text="Yes.")], None)
        self.assertEqual(transcriber.transcribe_chunk(audio), "Yes.")
        transcriber.model.transcribe.assert_called_once()


if __name__ == "__main__":
    unittest.main()
