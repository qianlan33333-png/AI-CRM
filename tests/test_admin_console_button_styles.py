import re
from pathlib import Path


ADMIN_CONSOLE_CSS = Path("aicrm_next/app/admin_console/static/admin_console/admin_console.css")


def test_primary_admin_buttons_keep_white_text_after_compact_override() -> None:
    css = ADMIN_CONSOLE_CSS.read_text(encoding="utf-8")
    primary_blocks = re.findall(r"\.admin-button--primary\s*\{([^}]*)\}", css, flags=re.S)

    assert primary_blocks
    assert "background: var(--brand);" in primary_blocks[-1]
    assert "color: #fff;" in primary_blocks[-1]
