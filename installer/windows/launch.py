"""Windows-only PyInstaller entry point for Simple Deck.

PyInstaller freezes the entry script as ``__main__`` with no package context,
so the relative imports in ``simple_deck/__main__.py`` (``from .app import ...``)
fail with ``ImportError: attempted relative import with no known parent package``.

This wrapper imports ``simple_deck.__main__`` via its absolute package path,
giving it the correct ``__package__`` context. Shared source is untouched.
"""
import sys

from simple_deck.__main__ import main

sys.exit(main())
