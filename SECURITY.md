# Security

Local Interview Transcriber is a local-first desktop app. It records audio, writes local files, and loads local Whisper models through faster-whisper.

## Security Model

- No cloud transcription or app-owned network service is used during recording/transcription.
- First model use may download model files through upstream model tooling.
- Source runs write to the project-local data folder.
- Packaged app runs write to `~/Library/Application Support/InterviewTranscriber/`.
- Transcript history, open, reveal, rename, autosave, and finalization are constrained to the app recordings folder.
- System and Mic + System modes require BlackHole or an equivalent third-party virtual audio device.

## Sensitive Data

Recordings and transcripts may contain private conversations. They are intentionally excluded from Git by `.gitignore`.

Before sharing logs, review `debug.log`; it can include local paths, device names, and error details.

## Dependency Checks

Before release, run:

```bash
python3 -m pip_audit
python3 -m pip_audit -r requirements.txt --no-deps
```

The release preparation pass for v0.1 found no known vulnerabilities in the installed Python environment or direct app requirements after upgrading local `pip`.

## Reporting Issues

This is a small proof-of-work project. If published publicly, use GitHub Issues or the repository owner's preferred contact path for security reports.
