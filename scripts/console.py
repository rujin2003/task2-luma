"""Make script output safe on consoles that are not UTF-8.

The demo narration uses box-drawing rules and status marks. On a Windows console the
default encoding is cp1252, which cannot encode them, and Python raises
`UnicodeEncodeError` mid-print -- so the demo dies partway through rather than merely
looking wrong. Reconfiguring the stream to UTF-8 with a replacement fallback keeps the
same output everywhere and degrades to `?` on a terminal that genuinely cannot render a
glyph.

Call `use_utf8_stdout()` once at the top of a script's entry point.
"""

from __future__ import annotations

import sys
from typing import IO, Any, cast


def use_utf8_stdout() -> None:
    """Force UTF-8 on stdout/stderr where the platform allows it."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - non-TextIOWrapper stream
            continue
        try:
            cast(Any, cast(IO[str], stream)).reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - stream already detached
            continue
