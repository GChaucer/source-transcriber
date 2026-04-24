import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000


class AudioRecorder:
    """Captures one or two local audio sources and emits mixed chunks."""

    def __init__(self, chunk_queue: queue.Queue, debug_log=None):
        self.chunk_queue = chunk_queue
        self._debug_log = debug_log
        self.recording = False
        self._raw_queue: queue.Queue = queue.Queue()
        self._wave_queue: queue.Queue = queue.Queue()
        self._stream = None
        self._streams: list[sd.InputStream] = []
        self._chunker_thread = None
        self._wave_thread = None
        self._mixer_thread = None
        self._chunk_samples = SAMPLE_RATE * 8  # default, overridden in start()
        self._first_callback = threading.Event()
        self._source_first_callbacks: dict[str, threading.Event] = {}
        self._source_queues: dict[str, queue.Queue] = {}
        self._callback_count = 0
        self._frames_received = 0
        self._source_frames_received: dict[str, int] = {}
        self._max_level = 0.0
        self._source_max_level: dict[str, float] = {}
        self._last_status = ""
        self._selected_input_device = None
        self._selected_input_devices: dict[str, dict] = {}
        self._audio_paths: dict[str, Path] = {}
        self._wave_files: dict[str, wave.Wave_write] = {}
        self._mode = "mic"

    def _log(self, message: str) -> None:
        if self._debug_log:
            self._debug_log(f"[recorder] {message}")

    def _reset_startup_metrics(self) -> None:
        self._first_callback.clear()
        self._source_first_callbacks = {}
        self._source_queues = {}
        self._callback_count = 0
        self._frames_received = 0
        self._source_frames_received = {}
        self._max_level = 0.0
        self._source_max_level = {}
        self._last_status = ""
        self._selected_input_device = None
        self._selected_input_devices = {}
        self._audio_paths = {}
        self._wave_files = {}
        self._streams = []
        self._stream = None

    def diagnostics_snapshot(self) -> dict:
        stream_active = None
        if self._streams:
            stream_active = all(bool(getattr(stream, "active", False)) for stream in self._streams)
        elif self._stream is not None:
            stream_active = bool(getattr(self._stream, "active", False))
        return {
            "recording": self.recording,
            "mode": self._mode,
            "stream_active": stream_active,
            "callback_count": self._callback_count,
            "frames_received": self._frames_received,
            "source_frames_received": dict(self._source_frames_received),
            "max_level": round(self._max_level, 6),
            "source_max_level": {
                source: round(level, 6) for source, level in self._source_max_level.items()
            },
            "last_status": self._last_status,
            "selected_input_device": self._selected_input_device,
            "selected_input_devices": dict(self._selected_input_devices),
            "audio_paths": {key: str(path) for key, path in self._audio_paths.items()},
        }

    def _resolve_input_device(self, device: int | None, label: str) -> tuple[int, dict]:
        if device is not None:
            input_index = device
            try:
                device_info = sd.query_devices(input_index, "input")
            except Exception as exc:
                self._log(f"{label} device query failed index={input_index}: {exc}")
                raise RuntimeError(f"Unable to open {label} audio device: {exc}") from exc
        else:
            try:
                default_device = sd.default.device
                devices = sd.query_devices()
                self._log(
                    f"default_device={default_device} enumerated_devices={len(devices)}"
                )
            except Exception as exc:
                self._log(f"device enumeration failed: {exc}")
                raise RuntimeError(f"Unable to enumerate audio devices: {exc}") from exc

            input_index = default_device[0] if isinstance(default_device, (list, tuple)) else default_device
            if input_index in (None, -1):
                raise RuntimeError(f"No default {label} input device is selected.")

            try:
                device_info = sd.query_devices(input_index, "input")
            except Exception as exc:
                self._log(f"default {label} device query failed index={input_index}: {exc}")
                raise RuntimeError(f"Unable to open default {label} input device: {exc}") from exc

        selected = {
            "index": input_index,
            "name": device_info.get("name"),
            "max_input_channels": device_info.get("max_input_channels"),
            "default_samplerate": device_info.get("default_samplerate"),
        }
        self._log(
            f"selected_{label}_device="
            f"{selected['index']} {selected['name']} "
            f"channels={selected['max_input_channels']} "
            f"default_sr={selected['default_samplerate']}"
        )

        try:
            sd.check_input_settings(
                device=input_index,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
            )
        except Exception as exc:
            self._log(f"{label} input settings check failed: {exc}")
            raise RuntimeError(f"{label.title()} input settings are not usable: {exc}") from exc

        return input_index, selected

    def _open_wave_writers(self, audio_paths: dict[str, Path] | None) -> None:
        if not audio_paths:
            return
        for source, path in audio_paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = wave.open(str(path), "wb")
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(SAMPLE_RATE)
            self._wave_files[source] = fh
            self._audio_paths[source] = path

    def _close_wave_writers(self) -> None:
        for fh in self._wave_files.values():
            try:
                fh.close()
            except Exception:
                pass
        self._wave_files = {}

    def _wave_writer(self) -> None:
        try:
            while True:
                item = self._wave_queue.get()
                if item is None:
                    break
                source, audio = item
                fh = self._wave_files.get(source)
                if fh is None:
                    continue
                pcm = np.clip(audio.reshape(-1), -1.0, 1.0)
                fh.writeframes((pcm * 32767.0).astype("<i2").tobytes())
        except Exception as exc:
            self._log(f"wave writer failed: {exc}")
        finally:
            self._close_wave_writers()

    def _record_source_metrics(self, source: str, frames: int, indata: np.ndarray, status) -> None:
        if status:
            self._last_status = str(status)
            self._log(f"{source} callback status={status}")
        self._callback_count += 1
        self._frames_received += frames
        self._source_frames_received[source] = self._source_frames_received.get(source, 0) + frames
        if indata.size:
            level = float(np.max(np.abs(indata)))
            self._max_level = max(self._max_level, level)
            self._source_max_level[source] = max(self._source_max_level.get(source, 0.0), level)
        event = self._source_first_callbacks.get(source)
        if event and not event.is_set():
            event.set()
            self._log(
                f"{source} first callback fired frames={frames} "
                f"max_level={self._source_max_level.get(source, 0.0):.6f}"
            )
        if self._callback_count == 1:
            self._first_callback.set()

    def _single_callback(self, source: str):
        def _callback(indata, frames, time_info, status):
            self._record_source_metrics(source, frames, indata, status)
            if self.recording:
                block = indata.copy()
                self._raw_queue.put(block)
                self._wave_queue.put((source, block))
                self._wave_queue.put(("mixed", block))

        return _callback

    def _dual_callback(self, source: str):
        def _callback(indata, frames, time_info, status):
            self._record_source_metrics(source, frames, indata, status)
            if self.recording:
                block = indata.copy()
                self._source_queues[source].put(block)
                self._wave_queue.put((source, block))

        return _callback

    def _mix_sources(self) -> None:
        mic_queue = self._source_queues["mic"]
        system_queue = self._source_queues["system"]
        try:
            while True:
                mic = mic_queue.get()
                system = system_queue.get()
                if mic is None or system is None:
                    break
                size = min(len(mic), len(system))
                if size <= 0:
                    continue
                mixed = (mic[:size] * 0.5) + (system[:size] * 0.5)
                self._wave_queue.put(("mixed", mixed))
                self._raw_queue.put(mixed)
        finally:
            self._raw_queue.put(None)

    def _chunker(self):
        accumulated = []
        total_samples = 0

        while True:
            block = self._raw_queue.get()
            if block is None:
                break
            accumulated.append(block.flatten())
            total_samples += len(block)

            if total_samples >= self._chunk_samples:
                chunk = np.concatenate(accumulated)
                self.chunk_queue.put(chunk[: self._chunk_samples])
                remainder = chunk[self._chunk_samples :]
                accumulated = [remainder] if len(remainder) > 0 else []
                total_samples = len(remainder)

        # Flush remaining audio if at least 1 second
        if accumulated and total_samples >= SAMPLE_RATE:
            self.chunk_queue.put(np.concatenate(accumulated))

        self.chunk_queue.put(None)

    def _wait_for_sources(self, sources: list[str], timeout: float) -> None:
        deadline = time.monotonic() + timeout
        for source in sources:
            remaining = max(0.0, deadline - time.monotonic())
            event = self._source_first_callbacks[source]
            if not event.wait(timeout=remaining):
                self._log(
                    f"startup timeout waiting for {source} callback after {timeout:.1f}s "
                    f"active={self.diagnostics_snapshot().get('stream_active')}"
                )
                self.stop()
                raise RuntimeError(
                    f"{source.title()} stream did not deliver audio frames after startup. "
                    "Check audio permissions, selected input devices, and virtual audio routing."
                )

    def start(
        self,
        chunk_seconds: int = 8,
        startup_timeout: float = 1.5,
        device: int | None = None,
        source_name: str = "mic",
        system_device: int | None = None,
        audio_paths: dict[str, Path] | None = None,
    ):
        self._chunk_samples = SAMPLE_RATE * chunk_seconds
        self._reset_startup_metrics()
        self._mode = "mic_system" if system_device is not None else source_name

        sources = ["mic", "system"] if system_device is not None else [source_name]
        for source in sources:
            self._source_first_callbacks[source] = threading.Event()
            self._source_frames_received[source] = 0
            self._source_max_level[source] = 0.0

        try:
            self._open_wave_writers(audio_paths)
            self._wave_thread = threading.Thread(target=self._wave_writer, daemon=True)
            self._wave_thread.start()

            if system_device is not None:
                mic_index, mic_info = self._resolve_input_device(device, "mic")
                system_index, system_info = self._resolve_input_device(system_device, "system")
                self._selected_input_devices = {"mic": mic_info, "system": system_info}
                self._selected_input_device = mic_info
                self._source_queues = {"mic": queue.Queue(), "system": queue.Queue()}
                self._streams = [
                    sd.InputStream(
                        samplerate=SAMPLE_RATE,
                        channels=1,
                        dtype="float32",
                        callback=self._dual_callback("mic"),
                        blocksize=1024,
                        device=mic_index,
                    ),
                    sd.InputStream(
                        samplerate=SAMPLE_RATE,
                        channels=1,
                        dtype="float32",
                        callback=self._dual_callback("system"),
                        blocksize=1024,
                        device=system_index,
                    ),
                ]
            else:
                input_index, device_info = self._resolve_input_device(device, source_name)
                self._selected_input_device = device_info
                self._selected_input_devices = {source_name: device_info}
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    callback=self._single_callback(source_name),
                    blocksize=1024,
                    device=input_index,
                )
                self._streams = [self._stream]

            self.recording = True
            for stream in self._streams:
                stream.start()
        except Exception as exc:
            self.recording = False
            for stream in list(self._streams):
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            self._stream = None
            self._streams = []
            if self._wave_thread:
                self._wave_queue.put(None)
                self._wave_thread.join(timeout=2.0)
            else:
                self._close_wave_writers()
            self._log(f"stream open/start failed: {exc}")
            raise RuntimeError(f"Unable to open audio stream: {exc}") from exc

        self._log(
            f"stream started mode={self._mode} "
            f"active={self.diagnostics_snapshot().get('stream_active')} "
            f"samplerate={SAMPLE_RATE} blocksize=1024"
        )

        self._wait_for_sources(sources, startup_timeout)

        if system_device is not None:
            self._mixer_thread = threading.Thread(target=self._mix_sources, daemon=True)
            self._mixer_thread.start()

        self._chunker_thread = threading.Thread(target=self._chunker, daemon=True)
        self._chunker_thread.start()
        self._log(
            f"capture ready callback_count={self._callback_count} "
            f"frames_received={self._frames_received} max_level={self._max_level:.6f}"
        )
        return self.diagnostics_snapshot()

    def stop(self):
        self._log(
            f"stop requested callback_count={self._callback_count} "
            f"frames_received={self._frames_received} max_level={self._max_level:.6f}"
        )
        self.recording = False
        for stream in list(self._streams):
            try:
                stream.stop()
            finally:
                stream.close()
        self._streams = []
        self._stream = None

        if self._source_queues:
            for source_queue in self._source_queues.values():
                source_queue.put(None)
            if self._mixer_thread:
                self._mixer_thread.join(timeout=2.0)
        else:
            self._raw_queue.put(None)

        self._wave_queue.put(None)
        if self._wave_thread:
            self._wave_thread.join(timeout=2.0)
