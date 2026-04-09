"""
python -m loudtqdm

Runs a fake workload so you can hear and see the full experience.
"""

import sys
import time

from . import loudtqdm

_BANNER = """\
+------------------------------------------+
|  L O U D T Q D M   v0.1.0               |
|  pip install loudtqdm                    |
+------------------------------------------+
"""

def main():
    print(_BANNER, file=sys.stderr)

    items = list(range(60))
    for _ in loudtqdm(items, desc="LOADING", reverse="--reverse" in sys.argv):
        time.sleep(0.12)

    print("DONE.", file=sys.stderr)


if __name__ == "__main__":
    main()
