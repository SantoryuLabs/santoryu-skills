"""Entry point for `python -m santoryu`, mirroring the `santoryu` console script."""

import sys

from santoryu.cli import main

if __name__ == "__main__":
    sys.exit(main())
