"""Menu bar item for the HUD window: status dot, phase, show/hide.

The HUD runs as an accessory app (no Dock icon), so once the window is
hidden the menu bar item is the only way back. AppKit is imported inside
functions only (`test_every_src_module_imports`: real AppKit aborts
without a window server) and every AppKit call runs on the main thread
through `AppHelper.callAfter`.
"""

from __future__ import annotations

from typing import Callable

DOING_MAX = 60

# the same colors the html light uses
DOT_RGB = {
    "green": (0.24, 0.86, 0.52),
    "yellow": (0.96, 0.77, 0.26),
    "red": (1.0, 0.42, 0.42),
    "grey": (0.55, 0.58, 0.63),
}


def title(data: dict) -> str:
    """Text after the dot in the menu bar: the phase, nothing else."""
    return str(data.get("phase_label") or "tero")


def menu_lines(data: dict) -> list[str]:
    """Two read-only lines on top of the menu: health and what is going on."""
    head = str(data.get("light_text") or "")
    label = data.get("phase_label")
    if label:
        head = f"{head} · фаза {label}" if head else f"фаза {label}"
    doing = " ".join(str(data.get("doing") or "").split())
    if len(doing) > DOING_MAX:
        doing = doing[: DOING_MAX - 1] + "…"
    return [head, doing]


AUTOSAVE_NAME = "tero-hud"
# points from the right edge of the menu bar; a new item goes leftmost by
# default, and on a notched MacBook with a full menu bar that is under the notch
PREFERRED_POSITION = 260


def _prefer_position() -> None:
    """Seed the position macOS restores for our autosave name; a user drag wins later."""
    from Foundation import NSUserDefaults

    defaults = NSUserDefaults.standardUserDefaults()
    key = f"NSStatusItem Preferred Position {AUTOSAVE_NAME}"
    if defaults.objectForKey_(key) is None:
        defaults.setDouble_forKey_(float(PREFERRED_POSITION), key)


_TARGET_CLS = None


def _target_cls():
    """NSObject subclass for menu actions; defined once (ObjC class names are global)."""
    global _TARGET_CLS
    if _TARGET_CLS is None:
        from Foundation import NSObject

        class TeroTrayTarget(NSObject):
            def toggle_(self, sender):
                self.on_toggle()

            def quit_(self, sender):
                self.on_quit()

        _TARGET_CLS = TeroTrayTarget
    return _TARGET_CLS


class Tray:
    """NSStatusItem with a colored dot. Build and update via `call` (main thread)."""

    def __init__(self, on_toggle: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self._on_toggle = on_toggle
        self._on_quit = on_quit
        self._item = None
        self._lines: list = []
        self._toggle_item = None

    @staticmethod
    def call(fn, *args) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(fn, *args)

    def build(self) -> None:
        import AppKit

        target = _target_cls().alloc().init()
        target.on_toggle = self._on_toggle
        target.on_quit = self._on_quit
        self._target = target
        _prefer_position()
        bar = AppKit.NSStatusBar.systemStatusBar()
        self._item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self._item.setAutosaveName_(AUTOSAVE_NAME)
        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        for _ in range(2):
            line = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
            line.setEnabled_(False)
            menu.addItem_(line)
            self._lines.append(line)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._toggle_item = self._action(menu, "Спрятать HUD", "toggle:")
        self._action(menu, "Закрыть HUD", "quit:")
        self._item.setMenu_(menu)
        self.update({"light": "grey", "phase_label": ""}, True)

    def _action(self, menu, text: str, selector: str):
        import AppKit

        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(text, selector, "")
        item.setTarget_(self._target)
        menu.addItem_(item)
        return item

    def update(self, data: dict, visible: bool) -> None:
        if self._item is None:
            return
        import AppKit

        r, g, b = DOT_RGB.get(str(data.get("light")), DOT_RGB["grey"])
        dot_color = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
        text = AppKit.NSMutableAttributedString.alloc().initWithString_("● " + title(data))
        text.addAttribute_value_range_(AppKit.NSForegroundColorAttributeName, dot_color, (0, 1))
        self._item.button().setAttributedTitle_(text)
        for line, value in zip(self._lines, menu_lines(data)):
            line.setTitle_(value)
            line.setHidden_(not value)
        self._toggle_item.setTitle_("Спрятать HUD" if visible else "Показать HUD")
