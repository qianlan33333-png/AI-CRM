from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next" / "automation" / "automation_engine" / "templates" / "admin_console" / "channel_code_form.html"
CHANNEL_JS = ROOT / "aicrm_next" / "automation" / "automation_engine" / "static" / "admin_console" / "channel_admission_pages.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_channel_form_uses_standard_send_content_composer_assets() -> None:
    html = _read(TEMPLATE)
    js = _read(CHANNEL_JS)

    assert "send_content_composer.js" in html
    assert "send_content_composer.css" in html
    assert "material_picker.js" in html
    assert "material_picker.css" in html
    assert "配置欢迎语和素材" in html
    assert "data-open-welcome-composer" in html
    assert "AICRMSendContentComposer.open" in js
    assert 'title: "配置欢迎语和素材"' in js
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


def test_channel_form_save_error_prefers_fastapi_detail() -> None:
    js = _read(CHANNEL_JS)

    assert "function apiErrorMessage(data, fallback)" in js
    assert "const detail = data && data.detail" in js
    assert 'const message = apiErrorMessage(data, "保存失败")' in js
    assert "data.error || data.reason || \"保存失败\"" not in js


def test_channel_form_no_longer_uses_private_welcome_material_picker() -> None:
    combined = _read(TEMPLATE) + "\n" + _read(CHANNEL_JS)

    forbidden = [
        "/api/admin/channel-" + "welcome-materials",
        "/api/admin/image-" + "library",
        "/api/admin/miniprogram-" + "library",
        "/api/admin/attachment-" + "library",
        "data-open-" + "miniprogram-picker",
        "data-open-" + "attachment-picker",
        "data-miniprogram-" + "selected",
        "data-attachment-" + "selected",
        "预览并选择" + "小程序",
        "预览并选择" + "图片/PDF",
    ]
    for marker in forbidden:
        assert marker not in combined


def test_channel_welcome_adapter_round_trips_standard_content_package() -> None:
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(CHANNEL_JS))}, "utf8");
const sandbox = {{
  window: {{}},
  document: {{ querySelector() {{ return null; }} }}
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const adapter = sandbox.window.AICRMChannelWelcomeAdapter;
const contentPackage = adapter.welcomeFieldsToContentPackage({{
  welcome_message: "  欢迎加入  ",
  welcome_image_library_ids: [12, "12", 34],
  welcome_miniprogram_library_ids: ["56"],
  welcome_attachment_library_ids: "78, 78, 90"
  ,welcome_group_invite_library_ids: [91]
}});
const fields = adapter.contentPackageToWelcomeFields({{
  content_text: "  新欢迎语  ",
  image_library_ids: ["101", 102],
  miniprogram_library_ids: [201],
  attachment_library_ids: ["301", "301", 302]
  ,group_invite_library_ids: [401]
}});
const empty = adapter.welcomeFieldsToContentPackage({{}});
console.log(JSON.stringify({{ contentPackage, fields, empty }}));
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
    assert payload["empty"] == {
        "content_text": "",
        "image_library_ids": [],
        "miniprogram_library_ids": [],
        "attachment_library_ids": [],
        "group_invite_library_ids": [],
    }


def test_channel_welcome_button_opens_composer_with_runtime_event_delegation() -> None:
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(CHANNEL_JS))}, "utf8");
const listeners = {{}};
const elements = {{}};
function element(name, value) {{
  return elements[name] || (elements[name] = {{
    value: value || "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    classList: {{ toggle() {{}} }},
    dataset: {{}},
    addEventListener(type, handler) {{ listeners[name + ":" + type] = handler; }},
    querySelector() {{ return null; }},
    querySelectorAll() {{ return []; }},
    closest(selector) {{ return selector === "[data-open-welcome-composer]" ? this : null; }},
  }});
}}
const root = {{
  dataset: {{ adminToken: "" }},
  querySelector(selector) {{
    if (selector === "[data-channel-bootstrap]") return {{ textContent: "{{}}" }};
    if (selector === "[data-open-welcome-composer]") return element("openButton");
    if (selector === "[data-welcome-message]") return element("message", "你好");
    if (selector === "[data-miniprogram-ids]") return element("mini", "34");
    if (selector === "[data-image-ids]") return element("image", "12");
    if (selector === "[data-attachment-ids]") return element("attachment", "56");
    if (selector === "[data-welcome-content-summary]") return element("summary");
    if (selector === "[data-channel-save-feedback]") return element("feedback");
    return null;
  }},
  querySelectorAll() {{ return []; }},
  addEventListener(type, handler) {{ listeners["root:" + type] = handler; }},
}};
let opened = false;
const sandbox = {{
  window: {{
    AICRMSendContentComposer: {{
      open(options) {{
        opened = true;
        if (options.value.content_text !== "你好") throw new Error("bad text");
        options.onConfirm({{
          content_text: "确认话术",
          image_library_ids: [12, 13],
          miniprogram_library_ids: [34],
          attachment_library_ids: [56],
        }});
      }},
    }},
  }},
  document: {{ querySelector(selector) {{ return selector === "[data-channel-admission-page]" ? root : null; }} }},
  navigator: {{}},
  console,
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
listeners["root:click"]({{
  preventDefault() {{}},
  target: element("openButton"),
}});
console.log(JSON.stringify({{
  opened,
  ready: root.dataset.welcomeComposerReady,
  message: element("message").value,
  images: element("image").value,
  summary: element("summary").innerHTML,
}}));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload["opened"] is True
    assert payload["ready"] == "1"
    assert payload["message"] == "确认话术"
    assert payload["images"] == "12,13"
    assert "图片 2" in payload["summary"]


def test_channel_welcome_missing_composer_sets_readable_error() -> None:
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(CHANNEL_JS))}, "utf8");
const listeners = {{}};
function makeElement(value) {{
  return {{
    value: value || "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    classList: {{ toggle() {{}} }},
    dataset: {{}},
    closest(selector) {{ return selector === "[data-open-welcome-composer]" ? this : null; }},
  }};
}}
const openButton = makeElement();
const feedback = makeElement();
const root = {{
  dataset: {{}},
  querySelector(selector) {{
    if (selector === "[data-channel-bootstrap]") return {{ textContent: "{{}}" }};
    if (selector === "[data-open-welcome-composer]") return openButton;
    if (selector === "[data-welcome-message]") return makeElement("");
    if (selector === "[data-miniprogram-ids]") return makeElement("");
    if (selector === "[data-image-ids]") return makeElement("");
    if (selector === "[data-attachment-ids]") return makeElement("");
    if (selector === "[data-welcome-content-summary]") return makeElement("");
    if (selector === "[data-channel-save-feedback]") return feedback;
    return null;
  }},
  querySelectorAll() {{ return []; }},
  addEventListener(type, handler) {{ listeners["root:" + type] = handler; }},
}};
const sandbox = {{
  window: {{}},
  document: {{ querySelector(selector) {{ return selector === "[data-channel-admission-page]" ? root : null; }} }},
  navigator: {{}},
  console,
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
listeners["root:click"]({{ preventDefault() {{}}, target: openButton }});
console.log(JSON.stringify({{ error: root.dataset.welcomeComposerError, feedback: feedback.textContent }}));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload["error"] == "composer_not_loaded"
    assert "标准内容编辑器未加载" in payload["feedback"]
