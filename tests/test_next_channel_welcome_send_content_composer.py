from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next/automation/automation_engine/templates/admin_console/channel_code_form.html"
CHANNEL_JS = ROOT / "aicrm_next/automation/automation_engine/static/admin_console/channel_admission_pages.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_channel_form_uses_inline_standard_send_content_composer() -> None:
    html = _read(TEMPLATE)
    js = _read(CHANNEL_JS)

    assert "send_content_composer.js" in html
    assert "send_content_composer.css" in html
    assert "material_picker.js" in html
    assert "material_picker.css" in html
    assert "data-welcome-composer-inline" in html
    assert "data-open-welcome-composer" not in html
    assert "AICRMSendContentComposer.mount" in js
    assert "AICRMSendContentComposer.open" not in js
    assert 'title: "欢迎语素材"' in js
    assert "maxTotal: 9" in js
    assert "onChange: syncHiddenFields" in js
    assert "safeInit" in js
    assert "welcomeComposerReady" in js
    assert "welcomeComposerError" in js
    assert "标准内容编辑器未加载，请刷新页面后重试" in js


def test_channel_form_exposes_auto_accept_friend_toggle() -> None:
    html = _read(TEMPLATE)
    js = _read(CHANNEL_JS)

    assert 'name="auto_accept_friend"' in html
    assert "扫码添加成员时自动通过好友申请" in html
    assert "skip_verify" in html
    assert "auto_accept_friend:" in js
    assert '[name="auto_accept_friend"]' in js


def test_channel_form_no_longer_uses_private_welcome_material_picker() -> None:
    combined = _read(TEMPLATE) + "\n" + _read(CHANNEL_JS)

    for marker in [
        "/api/admin/channel-" + "welcome-materials",
        "/api/admin/image-" + "library",
        "/api/admin/miniprogram-" + "library",
        "/api/admin/attachment-" + "library",
        "data-open-" + "miniprogram-picker",
        "data-open-" + "attachment-picker",
        "setup" + "WelcomeMaterialPicker",
    ]:
        assert marker not in combined


def test_channel_welcome_adapter_round_trips_standard_content_package() -> None:
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(CHANNEL_JS))}, "utf8");
const sandbox = {{ window: {{}}, document: {{ querySelector() {{ return null; }} }} }};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const adapter = sandbox.window.AICRMChannelWelcomeAdapter;
const contentPackage = adapter.welcomeFieldsToContentPackage({{
  welcome_message: "  欢迎加入  ",
  welcome_image_library_ids: [12, "12", 34],
  welcome_miniprogram_library_ids: ["56"],
  welcome_attachment_library_ids: "78, 78, 90",
  welcome_group_invite_library_ids: [91]
}});
const fields = adapter.contentPackageToWelcomeFields({{
  content_text: "  新欢迎语  ",
  image_library_ids: ["101", 102],
  miniprogram_library_ids: [201],
  attachment_library_ids: ["301", "301", 302],
  group_invite_library_ids: [401]
}});
console.log(JSON.stringify({{ contentPackage, fields }}));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload["contentPackage"] == {
        "content_text": "欢迎加入",
        "image_library_ids": [12, 34],
        "miniprogram_library_ids": [56],
        "attachment_library_ids": [78, 90],
        "group_invite_library_ids": [91],
    }
    assert payload["fields"] == {
        "welcome_message": "新欢迎语",
        "welcome_image_library_ids": [101, 102],
        "welcome_miniprogram_library_ids": [201],
        "welcome_attachment_library_ids": [301, 302],
        "welcome_group_invite_library_ids": [401],
    }


def test_channel_welcome_mounts_inline_and_syncs_hidden_fields() -> None:
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(CHANNEL_JS))}, "utf8");
const elements = {{}};
function element(name, value) {{
  return elements[name] || (elements[name] = {{
    value: value || "", textContent: "", innerHTML: "", hidden: false, dataset: {{}},
    classList: {{ toggle() {{}} }}, addEventListener() {{}}, querySelector() {{ return null; }},
    querySelectorAll() {{ return []; }}, closest() {{ return null; }}
  }});
}}
const root = {{
  dataset: {{ adminToken: "" }},
  querySelector(selector) {{
    if (selector === "[data-channel-bootstrap]") return {{ textContent: "{{}}" }};
    if (selector === "[data-welcome-composer-inline]") return element("mountPoint");
    if (selector === "[data-welcome-message]") return element("message", "你好");
    if (selector === "[data-miniprogram-ids]") return element("mini", "34");
    if (selector === "[data-image-ids]") return element("image", "12");
    if (selector === "[data-attachment-ids]") return element("attachment", "56");
    if (selector === "[data-group-invite-ids]") return element("group", "78");
    if (selector === "[data-channel-save-feedback]") return element("feedback");
    return null;
  }},
  querySelectorAll() {{ return []; }}, addEventListener() {{}},
}};
let mounted = false;
let initialValue = null;
let maxTotal = null;
const sandbox = {{
  window: {{
    AICRMSendContentComposer: {{ mount(container, options) {{
      mounted = container === element("mountPoint");
      initialValue = options.value;
      maxTotal = options.maxTotal;
      options.onChange({{
        content_text: "完整欢迎语第一行\\n完整欢迎语第二行",
        image_library_ids: [12, 13], miniprogram_library_ids: [34],
        attachment_library_ids: [56], group_invite_library_ids: [78]
      }});
    }} }},
  }},
  document: {{ querySelector(selector) {{ return selector === "[data-channel-admission-page]" ? root : null; }} }},
  navigator: {{}}, console,
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
console.log(JSON.stringify({{
  mounted, initialValue, maxTotal, ready: root.dataset.welcomeComposerReady,
  message: element("message").value, images: element("image").value, group: element("group").value
}}));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload["mounted"] is True
    assert payload["initialValue"]["content_text"] == "你好"
    assert payload["maxTotal"] == 9
    assert payload["ready"] == "1"
    assert payload["message"] == "完整欢迎语第一行\n完整欢迎语第二行"
    assert payload["images"] == "12,13"
    assert payload["group"] == "78"


def test_channel_welcome_missing_mount_api_sets_readable_error() -> None:
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(CHANNEL_JS))}, "utf8");
function element(value) {{ return {{ value: value || "", textContent: "", hidden: false, dataset: {{}}, classList: {{ toggle() {{}} }} }}; }}
const feedback = element();
const root = {{
  dataset: {{}},
  querySelector(selector) {{
    if (selector === "[data-channel-bootstrap]") return {{ textContent: "{{}}" }};
    if (selector === "[data-welcome-composer-inline]") return element();
    if (selector === "[data-welcome-message]" || selector === "[data-miniprogram-ids]" || selector === "[data-image-ids]" || selector === "[data-attachment-ids]" || selector === "[data-group-invite-ids]") return element();
    if (selector === "[data-channel-save-feedback]") return feedback;
    return null;
  }},
  querySelectorAll() {{ return []; }}, addEventListener() {{}},
}};
const sandbox = {{ window: {{}}, document: {{ querySelector(selector) {{ return selector === "[data-channel-admission-page]" ? root : null; }} }}, navigator: {{}}, console }};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
console.log(JSON.stringify({{ error: root.dataset.welcomeComposerError, feedback: feedback.textContent }}));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload["error"] == "composer_not_loaded"
    assert "标准内容编辑器未加载" in payload["feedback"]
