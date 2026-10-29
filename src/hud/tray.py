"""Menu bar item for the hidden HUD: a status dot that breathes like the HUD light.

The dot is there only while the window is hidden; a click on it brings the
window back, a right click opens the menu (health, newest line, quit).

The HUD runs as an accessory app (no Dock icon), so once the window is
hidden the menu bar item is the only way back. AppKit is imported inside
functions only (`test_every_src_module_imports`: real AppKit aborts
without a window server) and every AppKit call runs on the main thread
through `AppHelper.callAfter`.
"""

from __future__ import annotations

import math
import time
from typing import Callable

DOING_MAX = 60

# the same colors the html light uses
DOT_RGB = {
    "green": (0.24, 0.86, 0.52),
    "yellow": (0.96, 0.77, 0.26),
    "red": (1.0, 0.42, 0.42),
    "grey": (0.55, 0.58, 0.63),
}


# same breathing as `#light.green` in view.html: 2.4 s, opacity down to 0.45
BREATHE_S = 2.4
BREATHE_MIN = 0.45
TICK_S = 0.1


def dot_alpha(light: str, t: float) -> float:
    """Opacity of the menu bar dot at time t: green breathes, the rest stays lit."""
    if light != "green":
        return 1.0
    phase = (t % BREATHE_S) / BREATHE_S
    return BREATHE_MIN + (1.0 - BREATHE_MIN) * (0.5 + 0.5 * math.cos(2 * math.pi * phase))


def menu_lines(data: dict) -> list[str]:
    """Two read-only lines on top of the menu: health with the phase name, and what is going on."""
    head = str(data.get("light_text") or "")
    phase = str(data.get("phase_title") or "")
    if phase:
        head = f"{head} · {phase}" if head else phase
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

            def tick_(self, timer):
                self.on_tick()

            def click_(self, sender):
                self.on_click()

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
        self._light = "grey"
        self._timer = None

    @staticmethod
    def call(fn, *args) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(fn, *args)

    def build(self) -> None:
        import AppKit

        target = _target_cls().alloc().init()
        target.on_toggle = self._on_toggle
        target.on_quit = self._on_quit
        target.on_tick = self._paint
        target.on_click = self._click
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
        self._toggle_item = self._action(menu, "Показать HUD", "toggle:")
        self._action(menu, "Закрыть HUD", "quit:")
        # no setMenu_: a set menu swallows the left click; it pops up on a right click only
        self._menu = menu
        button = self._item.button()
        button.setTarget_(target)
        button.setAction_("click:")
        button.sendActionOn_(AppKit.NSEventMaskLeftMouseUp | AppKit.NSEventMaskRightMouseUp)
        self.update({"light": "grey"}, True)
        # common modes: the dot keeps breathing while the menu is open
        self._timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            TICK_S, target, "tick:", None, True
        )
        AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, AppKit.NSRunLoopCommonModes)

    def _action(self, menu, text: str, selector: str):
        import AppKit

        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(text, selector, "")
        item.setTarget_(self._target)
        menu.addItem_(item)
        return item

    def update(self, data: dict, visible: bool) -> None:
        if self._item is None:
            return
        self._light = str(data.get("light") or "grey")
        self._paint()
        for line, value in zip(self._lines, menu_lines(data)):
            line.setTitle_(value)
            line.setHidden_(not value)
        self.set_visible(visible)

    def set_visible(self, visible: bool) -> None:
        """The open window is its own light: the dot shows up only while it is hidden."""
        if self._item is None:
            return
        self._toggle_item.setTitle_("Спрятать HUD" if visible else "Показать HUD")
        if self._item.isVisible() == (not visible):
            return
        if not visible:
            # hiding an item drops its saved position; without one it comes back
            # leftmost, which on a notched MacBook is under the notch
            _prefer_position()
        self._item.setVisible_(not visible)

    def _click(self) -> None:
        import AppKit

        event = AppKit.NSApp.currentEvent()
        if event is not None and event.type() == AppKit.NSEventTypeRightMouseUp:
            self._item.popUpStatusItemMenu_(self._menu)
        else:
            self._on_toggle()

    def _paint(self) -> None:
        """Just the dot, no text: color from health, opacity from `dot_alpha`."""
        if self._item is None:
            return
        import AppKit

        r, g, b = DOT_RGB.get(self._light, DOT_RGB["grey"])
        alpha = dot_alpha(self._light, time.monotonic())
        dot_color = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, alpha)
        text = AppKit.NSMutableAttributedString.alloc().initWithString_("●")
        text.addAttribute_value_range_(AppKit.NSForegroundColorAttributeName, dot_color, (0, 1))
        self._item.button().setAttributedTitle_(text)
