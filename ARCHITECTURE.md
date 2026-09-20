# Architecture

Source is intentionally small. The v0.1 architecture keeps capture, transcription, and file management in separate modules without introducing a database or cloud service.

## Runtime Flow

```text
CustomTkinter UI
  -> AudioRecorder
       -> sounddevice / PortAudio input streams
       -> optional mic + system mixer
       -> WAV sidecar writer
       -> fixed-size mixed audio chunks
  -> Transcriber
       -> faster-whisper
       -> OpenAI Whisper model weights via CTranslate2
  -> transcript autosave
       -> Markdown
       -> text copy
       -> session JSON
  -> optional completed-transcript interpretation
       -> interpret.py / OpenRouter free model
       -> recordings/interpretations/*.md
```

## Main Modules

- `app.py` owns the UI, session lifecycle, path policy, transcript history, and user-facing errors.
- `recorder.py` owns local audio capture through `sounddevice`, startup diagnostics, source WAV files, and mixed chunks.
- `transcriber.py` owns the `faster-whisper` model wrapper and release-safe model choices.
- `transcriber.py` skips audio only if every 20 ms frame is below the silence threshold; short speech is not diluted by silence across a full chunk. WAV sidecars remain unchanged.
- `interpret.py` owns the optional `openrouter/free` request and separate result files. No custom model or paid fallback is accepted. Transcript-body hashes associate results with recordings after renames; the UI exposes them through a Summary tab. It does not participate in audio capture or transcription.
- Settings holds optional in-memory OpenRouter credentials and local model/update controls. The main window keeps input selection, elapsed time and per-source signal status visible.
- `system_audio.py` is an architecture note for a possible future native ScreenCaptureKit implementation. It is not active runtime code.

## Data Locations

Source runs write beside the project:

```text
recordings/
settings.json
debug.log
```

Packaged `.app` runs write to:

```text
~/Library/Application Support/Source/
```

The app should never rely on the shell current working directory for runtime writes.

## Audio Modes

- `mic`: captures the default microphone input.
- `system`: captures a BlackHole-style virtual system audio input.
- `mic_system`: captures default mic and virtual system audio separately, writes separate source WAVs, mixes them, and transcribes the mixed stream.

System and Mic + System do not use native macOS loopback capture yet. Users must provide BlackHole or an equivalent virtual audio device.
The UI checks the selected system input's observed signal level during recording and warns when it stays silent. Device availability alone cannot confirm that call audio is routed into BlackHole.

## Release Boundaries

The optional interpretation action is isolated from recording and requires an explicit send. Diarization, cloud sync, native ScreenCaptureKit capture, and advanced model tuning remain outside the current scope.
