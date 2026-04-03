from __future__ import annotations

"""Vendored recording subsystem for Echobox.

Derived from an MIT-licensed upstream recorder and adapted for Echobox:
- browser-first meeting detection
- BlackHole-preferred device selection
- WAV retention
- direct callback integration instead of an external CLI hook
"""

from .recorder import EchoboxRecorder
from .watcher import EchoboxWatcher

try:
    from .menubar import EchoboxMenuBar
except ImportError:
    EchoboxMenuBar = None  # type: ignore[assignment,misc]

__all__ = ["EchoboxRecorder", "EchoboxWatcher", "EchoboxMenuBar"]
