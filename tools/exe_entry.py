"""PyInstaller entry point (the package itself is started with python -m chatscribe)."""
import sys

from chatscribe.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
