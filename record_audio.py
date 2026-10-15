"""Record audio to a timestamped reports/ session as a 16-bit PCM WAV file.

Install: python -m pip install sounddevice
Run: python record_audio.py
List inputs: python record_audio.py --list-devices
"""

import argparse
from pathlib import Path
import queue
import sys
import threading
import wave

from session_report import configure_console, report_session


OUTPUT_DIR = Path(__file__).resolve().parent / "reports"
BLOCK_SIZE = 1024
SAMPLE_WIDTH = 2  # Signed 16-bit PCM, in bytes per sample.


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Value must be a positive integer.")
    return number


def device_id_or_name(value):
    try:
        return int(value)
    except ValueError:
        return value


class AudioRecorder:
    """Capture in a callback and write WAV blocks in a background worker."""

    def __init__(self, target, device=None, samplerate=None, channels=1, backend=None):
        self.target = Path(target)
        self.device = device
        self.rate = samplerate
        self.channels = channels
        self.backend = backend
        self.frames_written = 0
        self.overflow_count = 0
        self.dropped_blocks = 0
        self.error = None
        self.stream = None
        self.worker = None
        self.output = None
        self.recording = None
        self.blocks = queue.Queue(maxsize=128)
        self.finished = threading.Event()
        self.accepting = False
        self.stopped = False

    def _callback(self, data, frames, timing, status):
        if not self.accepting or self.error is not None:
            return
        if status:
            self.overflow_count += 1
        try:
            self.blocks.put_nowait(bytes(data))
        except queue.Full:
            self.dropped_blocks += 1

    def _write_blocks(self):
        try:
            while not self.finished.is_set() or not self.blocks.empty():
                try:
                    block = self.blocks.get(timeout=0.05)
                except queue.Empty:
                    continue
                self.recording.writeframesraw(block)
                self.frames_written += len(block) // (self.channels * SAMPLE_WIDTH)
        except Exception as error:
            self.error = error

    def start(self):
        if self.backend is None:
            import sounddevice
            self.backend = sounddevice
        sd = self.backend
        info = sd.query_devices(self.device, "input")
        if self.rate is None:
            self.rate = int(info["default_samplerate"])
        if self.channels > info["max_input_channels"]:
            raise ValueError(f"The selected input supports at most {info['max_input_channels']} channels.")
        sd.check_input_settings(device=self.device, channels=self.channels, dtype="int16", samplerate=self.rate)
        print(f"Audio input: {info['name']} | sample rate: {self.rate} Hz | channels: {self.channels} | PCM: 16-bit")
        try:
            self.stream = sd.RawInputStream(
                device=self.device, samplerate=self.rate, channels=self.channels,
                dtype="int16", blocksize=BLOCK_SIZE, callback=self._callback,
                finished_callback=self.finished.set,
            )
            self.target.parent.mkdir(parents=True, exist_ok=True)
            self.output = self.target.open("xb")
            self.recording = wave.open(self.output, "wb")
            self.recording.setnchannels(self.channels)
            self.recording.setsampwidth(SAMPLE_WIDTH)
            self.recording.setframerate(self.rate)
            self.worker = threading.Thread(target=self._write_blocks, name="wav-writer", daemon=True)
            self.worker.start()
            self.accepting = True
            self.stream.start()
            print(f"Audio recording started: {self.target}")
        except BaseException:
            self.stop()
            raise
        return self

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.accepting = False
        if self.stream is not None:
            for operation in (self.stream.stop, self.stream.close):
                try:
                    operation()
                except (Exception, KeyboardInterrupt) as error:
                    print(f"Audio cleanup error: {type(error).__name__}: {error}")
        self.finished.set()
        if self.worker is not None:
            # Drain all queued audio before finalizing the WAV header.
            while self.worker.is_alive():
                try:
                    self.worker.join(timeout=0.1)
                except KeyboardInterrupt:
                    print("Finishing WAV save; please wait...")
        for resource in (self.recording, self.output):
            if resource is not None:
                try:
                    resource.close()
                except (Exception, KeyboardInterrupt) as error:
                    self.error = error
                    print(f"WAV finalize error: {type(error).__name__}: {error}")
        if self.error is not None:
            print(f"Audio recording error: {type(self.error).__name__}: {self.error}")
        if self.overflow_count or self.dropped_blocks:
            print(f"Audio gaps possible: input status blocks={self.overflow_count}, dropped blocks={self.dropped_blocks}.")
        if self.output is not None:
            print(f"WAV file: {self.target}")
            print(f"Audio duration: {self.frames_written / self.rate:.3f}s | frames: {self.frames_written}")


def start_test_audio(target, device=None, samplerate=None, channels=1):
    """Audio failure should leave MIDI recording and analysis available."""
    try:
        return AudioRecorder(target, device, samplerate, channels).start()
    except Exception as error:
        print(f"Audio unavailable: {type(error).__name__}: {error}")
        print("Continuing the MIDI test without audio. Install sounddevice and check the audio input to save a WAV.")
        print(f"Python environment requiring the audio dependency: {sys.executable}")
        return None


def record_audio(sd, device=None, samplerate=None, channels=1, output_dir=None):
    with report_session(OUTPUT_DIR if output_dir is None else output_dir, label="Audio recording") as directory:
        input("Press Enter to start recording, then Ctrl+C to stop and save.")
        print()
        recorder = AudioRecorder(directory / "recording.wav", device, samplerate, channels, backend=sd)
        try:
            recorder.start()
            print("Recording... Press Ctrl+C to stop.")
            while not recorder.finished.wait(0.1):
                if recorder.error is not None:
                    break
        except KeyboardInterrupt:
            print("\nRecording stopped.")
        finally:
            recorder.stop()
        return 1 if recorder.error is not None else 0


def main():
    configure_console()
    parser = argparse.ArgumentParser(description="Press Enter to record audio; Ctrl+C saves a WAV to reports/.")
    parser.add_argument("--list-devices", action="store_true", help="List audio input devices and exit without recording.")
    parser.add_argument("--device", type=device_id_or_name, help="Input device ID or name; defaults to the system input.")
    parser.add_argument("--samplerate", type=positive_int, help="Sample rate in Hz; defaults to the input device's native rate.")
    parser.add_argument("--channels", type=positive_int, default=1, help="Number of channels (default: 1, mono).")
    args = parser.parse_args()
    try:
        import sounddevice as sd
    except ImportError:
        print("Missing audio dependency. Install it with: python -m pip install sounddevice")
        return 1

    try:
        if args.list_devices:
            default_input = sd.default.device[0]
            for index, info in enumerate(sd.query_devices()):
                if info["max_input_channels"] > 0:
                    marker = " (default)" if index == default_input else ""
                    print(
                        f"{index}: {info['name']} | max input channels: {info['max_input_channels']} | "
                        f"default sample rate: {info['default_samplerate']:g} Hz{marker}"
                    )
            return 0
        return record_audio(sd, args.device, args.samplerate, args.channels)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 0
    except Exception as error:
        print(f"Audio error: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
