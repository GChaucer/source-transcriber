import numpy as np

MODEL_SIZE = "small"       # Default v0.1 balance of quality and CPU speed.
MODEL_OPTIONS = ("small", "medium", "large-v3")
COMPUTE_TYPE = "int8"      # Best low-risk CPU-friendly setting for local runs.
LANGUAGE = "en"            # Skip auto-detect for faster/more stable English transcription.
MIN_AUDIO_RMS = 0.001      # -60 dBFS: don't ask Whisper to invent speech from near-silence.


class Transcriber:
    """Wraps faster-whisper for local CPU transcription."""

    def __init__(self):
        self.model = None
        self.model_name = MODEL_SIZE

    def set_model(self, model_name: str) -> None:
        if model_name not in MODEL_OPTIONS:
            raise ValueError(f"Unsupported model: {model_name}")
        if self.model_name != model_name:
            self.model_name = model_name
            self.model = None

    def load(self, progress_callback=None):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed. Run: pip install faster-whisper"
            )

        if progress_callback:
            progress_callback(f"Loading Whisper '{self.model_name}' model…")

        self.model = WhisperModel(
            self.model_name,
            device="cpu",
            compute_type=COMPUTE_TYPE,
        )

        if progress_callback:
            progress_callback("Model ready.")

    def transcribe_chunk(self, audio: np.ndarray, initial_prompt: str = "") -> str:
        if self.model is None:
            return ""
        if audio.size == 0:
            return ""
        # Check short frames so silence around a quiet utterance does not dilute it.
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        frame_samples = 320  # 20 ms at the recorder's 16 kHz sample rate.
        padded = np.pad(samples, (0, (-samples.size) % frame_samples))
        frame_rms = np.sqrt(np.mean(padded.reshape(-1, frame_samples) ** 2, axis=1))
        if float(np.max(frame_rms)) < MIN_AUDIO_RMS:
            return ""
        segments, _ = self.model.transcribe(
            audio,
            language=LANGUAGE,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt=initial_prompt or None,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
