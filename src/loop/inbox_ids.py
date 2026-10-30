"""Безопасные идентификаторы inbox (вынесено из inbox.py, план 09, фаза 1).

Ими же пользуется поток опроса (`src/relay/poller.py`): inbox.py реэкспортит
оба имени, старые импорты продолжают работать.
"""

from __future__ import annotations

import re
from typing import Any

_SAFE_UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _is_safe_uuid(uid: Any) -> bool:
    return isinstance(uid, str) and bool(_SAFE_UUID_RE.match(uid))
