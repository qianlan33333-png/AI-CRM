from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "cloud_campaigns_workspace.html"


def _source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_campaign_workspace_uses_standard_send_content_composer() -> None:
    source = _source()

    assert "send_content_composer.js" in source
    assert "send_content_composer.css" in source
    assert "material_picker.js" in source
    assert "material_picker.css" in source
    assert "AICRMSendContentComposer.open" in source
    assert "配置 Campaign Step 内容" in source
    assert "配置话术和素材" in source


def test_campaign_workspace_removes_private_step_material_pickers() -> None:
    source = _source()

    for forbidden in [
        "attach" + "MiniprogramPicker",
        "mount" + "ImagePicker",
        "load" + "MiniprogramLibrary",
        "/api/admin/miniprogram-" + "library",
        "/api/admin/image-" + "library",
        "/api/admin/attachment-" + "library",
        "edit-content-" + "text",
        "edit-image-" + "picker",
        "edit-" + "miniprogram-" + "ids",
    ]:
        assert forbidden not in source


def test_campaign_workspace_keeps_outer_step_controls_editable() -> None:
    source = _source()

    assert "edit-day-offset" in source
    assert "edit-send-time" in source
    assert "edit-stop-on-reply" in source
    assert "D+" in source
    assert "step.stop_on_reply" in source


def test_campaign_step_content_adapter_round_trips_legacy_and_standard_payloads() -> None:
    source = _source()
    match = re.search(r"<script>\s*(\(function \(\) \{[\s\S]*?\}\)\(\);)\s*</script>", source)
    assert match
    script = f"""
const vm = require("vm");
function makeEl() {{
  return {{
    value: "",
    hidden: false,
    textContent: "",
    innerHTML: "",
    dataset: {{}},
    className: "",
    style: {{}},
    addEventListener() {{}},
    querySelector() {{ return makeEl(); }},
    querySelectorAll() {{ return []; }},
    contains() {{ return false; }}
  }};
}}
const root = makeEl();
const sandbox = {{
  window: {{}},
  document: {{
    querySelector() {{ return root; }},
    createElement() {{ return makeEl(); }},
    body: {{ appendChild() {{}} }},
    addEventListener() {{}}
  }},
  fetch: async () => ({{ json: async () => ({{ ok: true, campaigns: [] }}) }}),
  alert() {{}},
  confirm() {{ return false; }},
  prompt() {{ return null; }},
  console,
  URLSearchParams,
  Date,
  JSON,
  parseInt,
  isNaN,
  encodeURIComponent
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(match.group(1))}, sandbox);
const adapter = sandbox.window.AICRMCampaignStepContentAdapter;
const fromLegacy = adapter.stepToContentPackage({{
  content_text: "  老 Campaign 话术  ",
  content_payload_json: {{
    image_library_ids: [12, "12", 34],
    miniprogram_library_ids: ["56"],
    attachment_library_ids: ["78", "78", 90],
    image_media_ids: ["legacy-media"]
  }}
}});
const fromContentPackage = adapter.stepToContentPackage({{
  content_package_json: {{
    content_text: "标准内容包",
    image_library_ids: [101],
    miniprogram_library_ids: [201],
    attachment_library_ids: [301]
  }}
}});
const toPayload = adapter.contentPackageToStepPayload({{
  content_text: "  保存内容  ",
  image_library_ids: ["1", "1", 2],
  miniprogram_library_ids: ["3"],
  attachment_library_ids: [4, "5"]
}});
console.log(JSON.stringify({{ fromLegacy, fromContentPackage, toPayload }}));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload["fromLegacy"] == {
        "content_text": "老 Campaign 话术",
        "image_library_ids": [12, 34],
        "miniprogram_library_ids": [56],
        "attachment_library_ids": [78, 90],
        "group_invite_library_ids": [],
    }
    assert payload["fromContentPackage"] == {
        "content_text": "标准内容包",
        "image_library_ids": [101],
        "miniprogram_library_ids": [201],
        "attachment_library_ids": [301],
        "group_invite_library_ids": [],
    }
    assert payload["toPayload"] == {
        "content_text": "保存内容",
        "content_package_json": {
            "content_text": "保存内容",
            "image_library_ids": [1, 2],
            "miniprogram_library_ids": [3],
            "attachment_library_ids": [4, 5],
            "group_invite_library_ids": [],
        },
        "content_package": {
            "content_text": "保存内容",
            "image_library_ids": [1, 2],
            "miniprogram_library_ids": [3],
            "attachment_library_ids": [4, 5],
            "group_invite_library_ids": [],
        },
        "image_library_ids": [1, 2],
        "image_media_ids": [],
        "miniprogram_library_ids": [3],
        "attachment_library_ids": [4, 5],
        "group_invite_library_ids": [],
    }
