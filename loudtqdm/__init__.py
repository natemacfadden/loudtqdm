"""
loudtqdm — tqdm but it beeps at you.

Usage::

    from loudtqdm import loudtqdm

    for item in loudtqdm(my_list, desc="crunching"):
        process(item)

Drop-in for tqdm.tqdm in the common iterator case.
Extra kwargs (``reverse``) are loudtqdm-specific; all others are ignored
so existing tqdm call-sites don't break.
"""

import sys
import time

from ._audio import ContinuousTone, play_jingle
from ._bar   import render, clear


__all__ = ["loudtqdm"]
__version__ = "0.1.0"


# Make the module itself callable so `import loudtqdm as tqdm; tqdm(items)`
# works as a drop-in for tqdm.
import sys as _sys
import types as _types

class _CallableModule(_types.ModuleType):
    def __call__(self, *args, **kwargs):
        return loudtqdm(*args, **kwargs)

_sys.modules[__name__].__class__ = _CallableModule


def loudtqdm(iterable=None, *, total=None, desc=None,
             file=sys.stderr, reverse: bool = False, **_kwargs):
    """
    Wrap *iterable* with an old-school ASCII progress bar and a rising
    square-wave tone that tracks completion.

    Parameters
    ----------
    iterable : iterable, optional
        The sequence to iterate over.
    total : int, optional
        Override the length hint.  Required when ``iterable`` has no
        ``__len__``.
    desc : str, optional
        Label printed before the bar.
    file : file-like, optional
        Where to write the bar (default: stderr).
    reverse : bool
        If True, the tone sweeps high → low instead of low → high.
    """
    if total is None and hasattr(iterable, "__len__"):
        total = len(iterable)

    tone = ContinuousTone(reverse=reverse)
    tone.start()

    start = time.monotonic()
    n = 0

    try:
        for item in iterable:
            render(n, total, desc, time.monotonic() - start, file=file)
            if total:
                tone.update(n / total)
            yield item
            n += 1

        # final render at 100 %
        render(n, total, desc, time.monotonic() - start, file=file)
        if total:
            tone.update(1.0)

    finally:
        tone.stop()
        tone.join(timeout=1.0)
        clear(file=file)
        play_jingle()   # non-daemon thread: Python waits for it; caller doesn't block
