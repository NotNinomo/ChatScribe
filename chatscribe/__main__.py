"""Entry point:  python -m chatscribe"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import DATA_DIR


def main() -> int:
    # A windowed exe has no console: stdout/stderr are None, and anything that prints
    # (logging, download progress bars) would crash. Point them at nothing.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[RotatingFileHandler(DATA_DIR / "chatscribe.log", maxBytes=2_000_000,
                                      backupCount=2, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )
    # Crisp text on high-DPI screens.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    from .app import ChatScribe
    from .ui import MainWindow

    app = ChatScribe()
    win = MainWindow(app)
    app.start()
    win.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
