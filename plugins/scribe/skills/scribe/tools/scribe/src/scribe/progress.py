"""Progress display on stderr (stdout stays clean for results).

- bar():       measurable work (audio seconds, chunks) -> tqdm bar with ETA
- heartbeat(): unmeasurable work (model load, one big forward) -> ticking elapsed time
Backend child processes inherit stderr, so their bars show up live in the parent's terminal.
"""

import contextlib
import sys
import threading

from tqdm import tqdm

TTY = sys.stderr.isatty()
# Non-TTY (an Agent capturing stderr): one line every 10 s instead of \r redraws.
INTERVAL = 0.2 if TTY else 10.0


def bar(total=None, desc="", unit="s"):
    return tqdm(total=total, desc=desc, unit=unit, file=sys.stderr, mininterval=INTERVAL,
                dynamic_ncols=True, ascii=not TTY, bar_format=None if total else "{desc}: {n:.0f}{unit} [{elapsed}]")


@contextlib.contextmanager
def heartbeat(desc):
    b = tqdm(desc=desc, file=sys.stderr, mininterval=INTERVAL, bar_format="{desc}: {elapsed} elapsed")
    stop = threading.Event()

    def tick():
        while not stop.wait(1 if TTY else INTERVAL):
            b.refresh()

    t = threading.Thread(target=tick, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join()
        b.close()
