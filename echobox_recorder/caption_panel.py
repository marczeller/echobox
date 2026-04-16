"""Floating live-caption panel for echobox.

A thin pyobjc NSPanel that displays the rolling WhisperKit stream from the
Swift capture helper while a call is being recorded. The panel is configured
as screen-share-invisible (`NSWindowSharingNone`) so it doesn't appear when
the user shares their screen in Zoom / Meet / Teams.

The panel is intentionally lightweight:
    - draw stable finalised text in one colour
    - draw the trailing partial in a dimmer colour
    - auto-scroll to the bottom
    - hide when the recording ends

All AppKit objects must be created and mutated on the main thread. The public
API is designed to be called from any thread; mutating methods hop to the main
thread via `performSelectorOnMainThread:withObject:waitUntilDone:`.
"""

from __future__ import annotations

import threading
from typing import Any

try:
    import AppKit  # type: ignore
    import Foundation  # type: ignore
    import objc  # type: ignore

    _HAS_APPKIT = True
except ImportError:  # pragma: no cover - AppKit always present on macOS
    _HAS_APPKIT = False


MAX_LINES = 40


class CaptionPanel:
    """Screen-share-invisible floating panel that streams live transcript text."""

    def __init__(self) -> None:
        if not _HAS_APPKIT:
            raise RuntimeError("AppKit not available — caption panel needs pyobjc")
        self._panel: Any = None
        self._text_view: Any = None
        self._finals: list[str] = []
        self._current_partial: str = ""
        self._status_line: str = "Recording…"
        self._lock = threading.Lock()
        self._visible = False
        self._create_panel()

    def _create_panel(self) -> None:
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskResizable
            | AppKit.NSWindowStyleMaskUtilityWindow
            | AppKit.NSWindowStyleMaskHUDWindow
        )
        rect = AppKit.NSMakeRect(40, 40, 520, 180)
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        panel.setTitle_("Echobox — Live Captions")
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setMovableByWindowBackground_(True)
        panel.setReleasedWhenClosed_(False)
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        # Critical: hide from screen-share recordings.
        panel.setSharingType_(AppKit.NSWindowSharingNone)
        # Also make the panel collectable across spaces without stealing focus.
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )

        scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 520, 180)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )

        text_view = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 520, 180)
        )
        text_view.setEditable_(False)
        text_view.setSelectable_(True)
        text_view.setRichText_(True)
        text_view.setDrawsBackground_(True)
        text_view.setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.07, 0.07, 0.09, 1.0
            )
        )
        text_view.setTextContainerInset_(AppKit.NSMakeSize(12, 10))
        text_view.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        text_view.textContainer().setWidthTracksTextView_(True)

        scroll.setDocumentView_(text_view)
        panel.setContentView_(scroll)

        self._panel = panel
        self._text_view = text_view
        self._redraw()

    def show(self) -> None:
        self._call_on_main(self._show_impl)

    def _show_impl(self) -> None:
        if self._panel is None:
            return
        self._panel.orderFrontRegardless()
        self._visible = True

    def hide(self) -> None:
        self._call_on_main(self._hide_impl)

    def _hide_impl(self) -> None:
        if self._panel is None:
            return
        self._panel.orderOut_(None)
        self._visible = False

    def set_status(self, status: str) -> None:
        with self._lock:
            self._status_line = status
        self._call_on_main(self._redraw)

    def reset(self) -> None:
        with self._lock:
            self._finals = []
            self._current_partial = ""
            self._status_line = "Recording…"
        self._call_on_main(self._redraw)

    def handle_event(self, event: dict[str, Any]) -> None:
        """Apply a JSONL event from the Swift helper to the panel state."""
        kind = event.get("type")
        if kind == "transcriber_loading":
            model = event.get("model", "")
            self.set_status(f"Loading transcriber ({model})…")
        elif kind == "transcriber_ready":
            self.set_status("Transcribing…")
        elif kind == "transcriber_error":
            msg = event.get("msg", "")
            self.set_status(f"Transcriber error: {msg}")
        elif kind == "partial":
            text = str(event.get("text", "")).strip()
            if text:
                with self._lock:
                    self._current_partial = text
                self._call_on_main(self._redraw)
        elif kind == "final":
            text = str(event.get("text", "")).strip()
            if text:
                with self._lock:
                    self._finals.append(text)
                    if len(self._finals) > MAX_LINES:
                        self._finals = self._finals[-MAX_LINES:]
                    # New final consumes the partial prefix.
                    self._current_partial = ""
                self._call_on_main(self._redraw)
        elif kind == "stopped":
            self.set_status("Recording ended")

    def _redraw(self) -> None:
        if self._text_view is None:
            return
        with self._lock:
            finals = list(self._finals)
            partial = self._current_partial
            status = self._status_line

        attributed = AppKit.NSMutableAttributedString.alloc().init()

        status_attrs = {
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.6, 0.9, 0.7, 1.0
            ),
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(
                11.0, AppKit.NSFontWeightMedium
            ),
        }
        attributed.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(
                status + "\n\n", status_attrs
            )
        )

        final_attrs = {
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.93, 0.95, 0.97, 1.0
            ),
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(14.0),
        }
        if finals:
            attributed.appendAttributedString_(
                AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    " ".join(finals) + " ", final_attrs
                )
            )

        if partial:
            partial_attrs = {
                AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.6, 0.65, 0.72, 1.0
                ),
                AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(14.0),
            }
            attributed.appendAttributedString_(
                AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    partial, partial_attrs
                )
            )

        self._text_view.textStorage().setAttributedString_(attributed)
        length = attributed.length()
        if length > 0:
            self._text_view.scrollRangeToVisible_((length - 1, 1))

    @staticmethod
    def _call_on_main(fn: Any) -> None:
        """Invoke a zero-arg callable on the AppKit main thread.

        Uses an NSObject performSelectorOnMainThread bridge. Safe from any
        thread; returns immediately without waiting.
        """
        if AppKit.NSThread.isMainThread():
            fn()
            return
        _MainThreadHop.alloc().initWithCallable_(fn).dispatch()


class _MainThreadHop(AppKit.NSObject):
    """Tiny Objective-C shim used to hop a Python callable to the main thread."""

    def initWithCallable_(self, fn):  # noqa: N802 - objc selector naming
        self = objc.super(_MainThreadHop, self).init()
        if self is None:
            return None
        self._callable = fn
        return self

    def dispatch(self):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "run", None, False
        )

    def run(self):
        try:
            self._callable()
        except Exception as exc:  # pragma: no cover - defensive
            print(f"caption_panel main-thread hop error: {exc}")
