"""The phone page is `view.html` plus a fixed `<head>` block (спек пульта 2, п. 6).

`test_view_html_is_self_contained` forbids `src=`, `href=` and `http` in the
source (the window opens through `html=`), while the phone needs a viewport,
a title, the manifest and `source.js`. So `relay/sync-hud.sh` writes a
committed copy `relay/public/index.html` = source with this block inserted
right after `<meta charset>`; a test checks the copy is exactly that.
"""

from __future__ import annotations

CHARSET = '<meta charset="utf-8">'
HEAD_BLOCK = (
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    "<title>теро-пульт</title>\n"
    '<link rel="manifest" href="/manifest.webmanifest">\n'
    '<script src="source.js"></script>'
)


def make_copy(source: str) -> str:
    """`source` with HEAD_BLOCK on its own lines after the charset meta."""
    if CHARSET not in source:
        raise ValueError("view.html без <meta charset>")
    return source.replace(CHARSET, CHARSET + "\n" + HEAD_BLOCK, 1)
