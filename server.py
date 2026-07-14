"""Compatibility launcher for ``python server.py``.

The application implementation lives in :mod:`backend.app` so imports, web
assets, and deployment entry points have a clear home.
"""

from backend.app import app, main

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
