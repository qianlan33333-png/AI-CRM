from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "aicrm_next/app/admin_console/static/admin_console/admin_download.js"
CENTER_TEMPLATE = ROOT / "aicrm_next/automation/automation_engine/templates/admin_console/channel_code_center.html"
FORM_TEMPLATE = ROOT / "aicrm_next/automation/automation_engine/templates/admin_console/channel_code_form.html"
CENTER_JS = ROOT / "aicrm_next/automation/automation_engine/static/admin_console/channel_code_center_next.js"
FORM_JS = ROOT / "aicrm_next/automation/automation_engine/static/admin_console/channel_admission_pages.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_channel_list_and_form_use_shared_blob_download_without_navigation() -> None:
    helper = _read(HELPER)
    center_html = _read(CENTER_TEMPLATE)
    form_html = _read(FORM_TEMPLATE)
    center_js = _read(CENTER_JS)
    form_js = _read(FORM_JS)

    assert "admin_download.js" in center_html
    assert "admin_download.js" in form_html
    assert "data-download-channel-qrcode" in center_js
    assert "data-download-channel-qrcode" in form_html
    assert "AICRMAdminDownload.download" in center_js
    assert "AICRMAdminDownload.download" in form_js
    assert "response.blob()" in helper
    assert "URL.createObjectURL(blob)" in helper
    assert "anchor.download = filename" in helper
    assert 'redirect: "error"' in helper
    assert "window.open" not in helper
    assert "window.location" not in helper


def test_shared_download_uses_utf8_attachment_filename_and_revokes_object_url() -> None:
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(HELPER))}, "utf8");
let clicked = false;
let revoked = "";
const anchor = {{ hidden: false, click() {{ clicked = true; }}, remove() {{}} }};
const sandbox = {{
  window: {{ setTimeout(fn) {{ fn(); }} }},
  document: {{ createElement() {{ return anchor; }}, body: {{ appendChild() {{}} }} }},
  URL: {{ createObjectURL() {{ return "blob:test"; }}, revokeObjectURL(value) {{ revoked = value; }} }},
  fetch: async () => ({{
    ok: true,
    headers: {{ get() {{ return "attachment; filename=channel-1-qrcode.jpg; filename*=UTF-8''%E6%B8%A0%E9%81%93%E7%A0%81.jpg"; }} }},
    blob: async () => ({{ size: 128, type: "image/jpeg" }}),
  }}),
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
sandbox.window.AICRMAdminDownload.download("/api/download").then((result) => {{
  console.log(JSON.stringify({{ clicked, revoked, filename: result.filename, anchorDownload: anchor.download }}));
}});
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload == {
        "clicked": True,
        "revoked": "blob:test",
        "filename": "渠道码.jpg",
        "anchorDownload": "渠道码.jpg",
    }
