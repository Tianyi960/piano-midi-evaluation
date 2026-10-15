"""Create a separate timestamped report folder and mirror terminal output to it."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime
import io
from pathlib import Path
import sys
import threading
import traceback


REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def configure_console():
    # Some Windows terminals cannot encode every character in audio device names.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")


def create_session_directory(root=None):
    directory = Path(root) if root is not None else REPORTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    # Follow the computer's local clock and time zone, including daylight saving.
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    for suffix in range(1000):
        name = stamp if suffix == 0 else f"{stamp}_{suffix:03d}"
        target = directory / name
        try:
            target.mkdir()
            return target
        except FileExistsError:
            continue
    raise FileExistsError("Could not create a unique report directory.")


class TeeStream(io.TextIOBase):
    """Keep output visible while saving it immediately to a UTF-8 log."""

    def __init__(self, terminal, log, lock):
        self.terminal = terminal
        self.log = log
        self.lock = lock

    @property
    def encoding(self):
        return getattr(self.terminal, "encoding", None) or "utf-8"

    def writable(self):
        return True

    def write(self, text):
        with self.lock:
            self.log.write(text)
            self.log.flush()
            self.terminal.write(text)
            self.terminal.flush()
        return len(text)

    def flush(self):
        with self.lock:
            if not self.log.closed:
                self.log.flush()
            if not getattr(self.terminal, "closed", False):
                self.terminal.flush()


@contextmanager
def report_session(root=None, label="Test"):
    configure_console()
    directory = create_session_directory(root)
    with (directory / "output.txt").open("x", encoding="utf-8", buffering=1) as log:
        lock = threading.RLock()
        with redirect_stdout(TeeStream(sys.stdout, log, lock)), redirect_stderr(TeeStream(sys.stderr, log, lock)):
            print(f"Session: {label}")
            print(f"Started (computer local time): {datetime.now().astimezone().isoformat()}")
            print(f"Python executable: {sys.executable}")
            print(f"Report directory: {directory}")
            print(f"Terminal output saved to: {directory / 'output.txt'}")
            try:
                yield directory
            except BaseException:
                # The caller still receives the exception; retain its traceback
                # even if Python prints the final traceback after redirection ends.
                traceback.print_exc(file=log)
                raise
            finally:
                print(f"Session finished (computer local time): {datetime.now().astimezone().isoformat()}")
