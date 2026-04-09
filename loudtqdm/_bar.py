"""
Progress bar renderer for loudtqdm.

Old-school ASCII only — no Unicode blocks, no colour.
Writes to stderr so it doesn't interfere with stdout.
"""

import sys
import time


_BAR_WIDTH = 30   # fill characters inside the brackets


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def render(n: int, total: int | None, desc: str | None,
           elapsed: float, file=sys.stderr):
    """
    Render one line of progress to *file* (default stderr).
    Overwrites the current line with \r.
    """
    prefix = f"{desc}: " if desc else ""

    if total:
        pct  = n / total
        fill = int(_BAR_WIDTH * pct)
        bar  = "#" * fill + "-" * (_BAR_WIDTH - fill)
        line = (
            f"\r{prefix}"
            f"[{bar}] "
            f"{n}/{total} "
            f"{int(pct * 100):3d}% "
            f"[{_fmt_elapsed(elapsed)}]"
        )
    else:
        # unknown total — spinning counter
        spinner = r"-\|/"[n % 4]
        line = f"\r{prefix}{spinner} {n} [{_fmt_elapsed(elapsed)}]"

    file.write(line)
    file.flush()


_CLEAR_WIDTH = _BAR_WIDTH + 40  # wide enough for prefix + bar + stats


def clear(file=sys.stderr):
    file.write("\r" + " " * _CLEAR_WIDTH + "\r")
    file.flush()
