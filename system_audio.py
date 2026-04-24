"""
System Audio Capture — Phase 2 Architecture (not yet implemented)

Phase 1 (current): System and Mic + System modes use sounddevice with a virtual
loopback device (BlackHole). The user installs BlackHole, routes Mac audio
output through it, and recorder.py captures from it as an input device. Mic +
System opens the default mic plus the virtual system device, writes separate
source WAV files, mixes both streams, and sends the mixed stream through the
existing transcription path. No new permissions or entitlements are required.

Phase 2 (this file): Native ScreenCaptureKit capture via pyobjc. No external
driver required. One-time Screen Recording permission prompt on first use.

──────────────────────────────────────────────────────────────────────────────
PHASE 2 — ScreenCaptureKit via pyobjc
──────────────────────────────────────────────────────────────────────────────

Dependencies:
    pip install pyobjc-framework-ScreenCaptureKit pyobjc-framework-AVFoundation

New permissions required:
    • com.apple.security.screen-recording (entitlement in app bundle)
    • NSScreenCaptureUsageDescription key in Info.plist

Packaging changes (build_macos_app.sh / .spec):
    • Add entitlement: com.apple.security.screen-recording = True
    • Add to Info.plist: NSScreenCaptureUsageDescription = "Interview Transcriber
      needs Screen Recording access to capture system audio during interviews."
    • codesign --entitlements entitlements.plist after PyInstaller build

Minimum macOS: 12.3 (Monterey). User's machine: Sequoia 15 — fully supported.

──────────────────────────────────────────────────────────────────────────────
HOW THE AUDIO FLOWS INTO THE EXISTING PIPELINE
──────────────────────────────────────────────────────────────────────────────

The existing recorder.py _chunker thread accumulates numpy float32 arrays and
pushes fixed-size chunks to chunk_queue. The SCK path must produce the same
format: mono float32 at 16000 Hz, pushed to the same chunk_queue.

    SCStream audio callback (CMSampleBuffer)
        → extract AudioBufferList
        → convert Int16/Float32 PCM → numpy float32
        → resample to 16000 Hz if needed (SCStreamConfiguration can set sampleRate)
        → push to _raw_queue  ← same queue as recorder._raw_queue
        → existing _chunker thread handles the rest

──────────────────────────────────────────────────────────────────────────────
IMPLEMENTATION SKETCH (pseudocode)
──────────────────────────────────────────────────────────────────────────────

from ScreenCaptureKit import (
    SCShareableContent,
    SCStream,
    SCStreamConfiguration,
    SCContentFilter,
)
from AVFoundation import AVAudioFormat

class SystemAudioCapture:
    def __init__(self, raw_queue: queue.Queue, debug_log=None):
        self._raw_queue = raw_queue
        self._stream: SCStream | None = None

    def start(self):
        # 1. Get shareable content (async, use semaphore or run loop)
        SCShareableContent.getShareableContentWithCompletionHandler_(handler)

        # 2. Build content filter — exclude current process audio
        config = SCStreamConfiguration.alloc().init()
        config.setCapturesAudio_(True)
        config.setExcludesCurrentProcessAudio_(True)
        config.setSampleRate_(16000)
        config.setChannelCount_(1)

        # 3. Use display-level filter (captures all system audio, no per-app picker)
        content_filter = SCContentFilter.alloc().initWithDisplay_excludingApplications_excludingWindows_(
            display, [], []
        )

        # 4. Create stream, add self as output delegate
        self._stream = SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, config, self
        )
        self._stream.addStreamOutput_type_sampleHandlerQueue_error_(self, 0, queue, None)
        self._stream.startCaptureWithCompletionHandler_(handler)

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
        # Convert CMSampleBuffer → numpy float32 mono at 16kHz
        # Push to self._raw_queue — picked up by existing _chunker thread
        audio = _sample_buffer_to_numpy(sample_buffer)
        if audio is not None:
            self._raw_queue.put(audio)

    def stop(self):
        if self._stream:
            self._stream.stopCaptureWithCompletionHandler_(None)
            self._stream = None
        self._raw_queue.put(None)  # sentinel to shut down _chunker

──────────────────────────────────────────────────────────────────────────────
WHAT DEFERS TO LATER
──────────────────────────────────────────────────────────────────────────────

• Per-app audio filtering (capture only Zoom, Chrome, etc.) — display-level
  filter captures all system audio, which is correct for interview use
• Mic + System via BlackHole — implemented in recorder.py for v0.1
• Mic + System via native ScreenCaptureKit — defer
• Speaker diarization — unrelated, defer to much later
• Automatic BlackHole detection fallback — Phase 1 handles that path
"""
