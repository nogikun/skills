"""Progress display on stderr (stdout stays clean for results), mirrored to the job's progress.json.

- bar():       measurable work (audio seconds, chunks) -> tqdm bar with ETA
- heartbeat(): unmeasurable work (model load, one big forward) -> ticking elapsed time
- context():   where/what to report (env vars, so backend child processes inherit it)
Backend child processes inherit stderr, so their bars show up live in the parent's terminal.
progress.json is what `scribe serve` reads; writing it is best-effort and never fails the work.
"""

import contextlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

TTY = sys.stderr.isatty()
# Non-TTY (an Agent capturing stderr): one line every 10 s instead of \r redraws.
INTERVAL = 0.2 if TTY else 10.0
FILE_INTERVAL = 1.0  # progress.json at most once a second
_last = 0.0


def context(path=None, **ctx) -> None:
    """Set the progress file and merge ctx (stage, attempt, ...) into what every report carries."""
    if path:
        os.environ["SCRIBE_PROGRESS"] = str(path)
    merged = {**json.loads(os.environ.get("SCRIBE_PROGRESS_CTX") or "{}"), **ctx}
    os.environ["SCRIBE_PROGRESS_CTX"] = json.dumps(merged, ensure_ascii=False)
    report(force=True)


def clear() -> None:
    os.environ.pop("SCRIBE_PROGRESS", None)
    os.environ.pop("SCRIBE_PROGRESS_CTX", None)


def report(force=False, desc=None, n=None, total=None, unit=None, text=None) -> None:
    global _last
    path = os.environ.get("SCRIBE_PROGRESS")
    now = time.monotonic()
    if not path or (not force and now - _last < FILE_INTERVAL):
        return
    _last = now
    data = {**json.loads(os.environ.get("SCRIBE_PROGRESS_CTX") or "{}"),
            "desc": desc, "n": n, "total": total, "unit": unit, "text": text,
            "updated_at": datetime.now(timezone.utc).isoformat()}
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:  # job dir gone, or a reader holds the file on Windows: next tick retries
        with contextlib.suppress(OSError):
            tmp.unlink()


class _Bar(tqdm):
    def _report(self, force=False):
        report(force, self.desc, round(self.n, 1), self.total, self.unit, self.postfix or None)

    def update(self, n=1):
        r = super().update(n)
        self._report()
        return r

    def close(self):
        if not self.disable:
            self._report(force=True)
        super().close()


def bar(total=None, desc="", unit="s"):
    return _Bar(total=total, desc=desc, unit=unit, file=sys.stderr, mininterval=INTERVAL,
                dynamic_ncols=True, ascii=not TTY, bar_format=None if total else "{desc}: {n:.0f}{unit} [{elapsed}]")


@contextlib.contextmanager
def heartbeat(desc):
    b = _Bar(desc=desc, file=sys.stderr, mininterval=INTERVAL, bar_format="{desc}: {elapsed} elapsed")
    stop = threading.Event()

    def tick():
        k = 0
        while not stop.wait(FILE_INTERVAL):
            k += 1
            report(force=True, desc=desc)
            if TTY or k % round(INTERVAL / FILE_INTERVAL) == 0:
                b.refresh()

    t = threading.Thread(target=tick, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join()
        b.close()
