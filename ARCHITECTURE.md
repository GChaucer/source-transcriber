# Architecture

Local Interview Transcriber is intentionally small. The v0.1 architecture keeps capture, transcription, and file management in separate modules without introducing a database or cloud service.

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
```

## Main Modules

- `app.py` owns the UI, session lifecycle, path policy, transcript history, and user-facing errors.
- `recorder.py` owns local audio capture through `sounddevice`, startup diagnostics, source WAV files, and mixed chunks.
- `transcriber.py` owns the `faster-whisper` model wrapper and release-safe model choices.
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
~/Library/Application Support/InterviewTranscriber/
```

The app should never rely on the shell current working directory for runtime writes.

## Audio Modes

- `mic`: captures the default microphone input.
- `system`: captures a BlackHole-style virtual system audio input.
- `mic_system`: captures default mic and virtual system audio separately, writes separate source WAVs, mixes them, and transcribes the mixed stream.

System and Mic + System do not use native macOS loopback capture yet. Users must provide BlackHole or an equivalent virtual audio device.

## Release Boundaries

v0.1 intentionally excludes diarization, summaries, cloud sync, native ScreenCaptureKit capture, and advanced model tuning. Those can be considered later only after the local capture path is boringly reliable.
