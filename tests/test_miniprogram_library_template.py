"""miniprogram_library 模板 contract 测试。

不起 Flask app，直接 grep 模板源码，确保 UI 关键结构不被无意撤回。跟
``test_image_library_template.py`` 同套路（图片库 PR-F/G 后已经走过同样
的整洁化路径，本测试给小程序库做镜像保护）。
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "miniprogram_library.html"


@pytest.fixture(scope="module")
def source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


# ---------- 标题去重 + 顶部工具栏 ---------- #

def test_no_duplicate_inline_page_header(source: str):
    """admin shell 已经渲染面包屑 + page header，模板内不应再放
    <h1>{{page_title}}</h1> + <p class="subtle">{{page_summary}}</p>。"""
    assert "{{ page_title }}" not in source
    assert "{{ page_summary }}" not in source


def test_toolbar_has_create_button(source: str):
    """页面唯一新增入口是这个按钮，点击打开蒙版 modal。"""
    assert 'id="mp-open-create"' in source
    assert 'class="mp-create-btn"' in source


def test_no_inline_create_form_left_in_page(source: str):
    """旧版铺平的 mp-form 已经收敛进 modal，不应再裸露在页面正文。"""
    assert 'class="mp-form"' not in source
    assert 'id="mp-create-form"' not in source


# ---------- 新增 Modal ---------- #

def test_create_modal_present_with_required_fields(source: str):
    assert 'id="mp-create-modal"' in source
    assert 'id="mp-create-name"' in source
    assert 'id="mp-create-appid"' in source
    assert 'id="mp-create-pagepath"' in source
    assert 'id="mp-create-title"' in source
    assert 'id="mp-create-thumb-picker"' in source


def test_create_modal_submits_to_post_endpoint(source: str):
    """新增提交必须 POST /api/admin/miniprogram-library 带 4 字段 + thumb_image_id。"""
    assert "/api/admin/miniprogram-library" in source
    assert "method: 'POST'" in source
    assert "thumb_image_id: thumbId" in source
    assert "requestJSON" in source


def test_create_submit_uses_timeout_and_does_not_wait_for_list_before_close(source: str):
    assert "requestJsonWithTimeout" in source
    assert "client.requestJsonWithTimeout || client.requestJson" in source
    success_idx = source.index("setCreateStatus('已新增")
    refresh_idx = source.index("refreshListInBackground();", success_idx)
    close_idx = source.index("setTimeout(closeCreateModal, 600)", success_idx)
    assert success_idx < refresh_idx < close_idx
    assert "await loadList();\n        setTimeout(closeCreateModal" not in source


def test_json_request_helper_falls_back_to_old_upload_client(source: str):
    """静态资源缓存可能让页面拿到旧 image_upload_client.js，模板不能直接崩。"""
    assert "function requestJSON" in source
    assert "client.requestJsonWithTimeout || client.requestJson" in source
    assert "图片上传客户端未加载" in source


def test_create_modal_has_open_close_handlers(source: str):
    assert 'id="mp-create-close"' in source
    assert 'id="mp-create-cancel"' in source
    assert "openCreateModal" in source
    assert "closeCreateModal" in source


# ---------- 工具栏 / 筛选 ---------- #

def test_toolbar_has_keyword_search(source: str):
    assert 'id="mp-q"' in source
    assert 'type="search"' in source


def test_toolbar_has_include_disabled_checkbox(source: str):
    assert 'id="mp-include-disabled"' in source


def test_toolbar_has_reset_button(source: str):
    assert 'id="mp-reset"' in source


# ---------- 网格卡片 ---------- #

def test_card_grid_layout(source: str):
    """卡片是网格布局（不是旧版垂直列表）。"""
    assert "mp-grid" in source
    assert "grid-template-columns:repeat(auto-fill" in source


def test_card_disabled_state_visually_marked(source: str):
    """停用图必须有视觉区分（灰度 + "已停用"角标）。"""
    assert "mp-card.disabled" in source
    assert "grayscale" in source
    assert "已停用" in source


def test_card_click_opens_edit_modal(source: str):
    """整张卡片点击进编辑 modal（替代旧版底部 toggle/resolve/delete 按钮）。"""
    assert "card.addEventListener('click'" in source
    assert "openEditModal" in source


def test_card_no_inline_action_buttons(source: str):
    """卡片底部不再有 toggle / resolve / delete 按钮（已移到编辑 modal 里）。"""
    assert 'data-action="toggle"' not in source
    assert 'data-action="delete"' not in source
    assert 'data-action="resolve"' not in source


# ---------- 编辑 / 详情 Modal（大窗）---------- #

def test_edit_modal_present_with_form_fields(source: str):
    assert 'id="mp-edit-modal"' in source
    assert 'id="mp-edit-name"' in source
    assert 'id="mp-edit-appid"' in source
    assert 'id="mp-edit-pagepath"' in source
    assert 'id="mp-edit-card-title"' in source
    assert 'id="mp-edit-thumb-picker"' in source


def test_edit_modal_has_two_column_layout(source: str):
    assert "mp-edit-layout" in source
    assert "mp-edit-image" in source
    assert "mp-edit-meta" in source


def test_edit_modal_shows_large_image(source: str):
    assert 'id="mp-edit-image-wrap"' in source
    assert "object-fit:contain" in source or "object-fit: contain" in source


def test_edit_modal_has_all_action_buttons(source: str):
    """编辑 modal 必须含：删除 / 刷新缩略图 / 启停 / 保存。"""
    assert 'id="mp-edit-delete"' in source
    assert 'id="mp-edit-resolve"' in source
    assert 'id="mp-edit-toggle"' in source
    assert 'id="mp-edit-save"' in source


def test_edit_modal_save_calls_put_endpoint(source: str):
    """保存按钮必须 PUT 到 /api/admin/miniprogram-library/<id> 带 4 字段。"""
    assert "method: 'PUT'" in source
    assert "/api/admin/miniprogram-library/" in source
    assert "refreshListInBackground();" in source
    save_start = source.index("document.getElementById('mp-edit-save')")
    save_end = source.index("// 启用 / 停用", save_start)
    save_block = source[save_start:save_end]
    assert "await loadList();\n        setTimeout(closeEditModal" not in save_block


def test_edit_modal_resolve_calls_test_resolve_endpoint(source: str):
    """刷新缩略图按钮调 /test-resolve（重传企微，刷新 thumb_media_id 缓存）。"""
    assert "/test-resolve" in source


def test_edit_modal_clickaway_and_esc_close(source: str):
    assert "closeEditModal" in source
    assert "Escape" in source


# ---------- 缩略图加载逻辑 ---------- #

def test_thumbnail_loader_uses_variant_urls(source: str):
    """缩略图直接使用后端列表返回的变体 URL，不再批量拉 image detail 原图。"""
    assert "thumb_320_url" in source
    assert "thumb_160_url" in source
    assert "preview_url" in source
    assert "/api/admin/image-library/' + imgId" not in source
    assert "thumb_image_id" in source


def test_thumbnail_legacy_fallbacks_kept(source: str):
    """老数据兜底字段 thumb_image_url / thumb_image_base64 仍然 fallback。"""
    assert "thumb_image_url" in source
    assert "thumb_image_base64" in source


def test_thumbnail_img_uses_lazy_responsive_attrs(source: str):
    assert 'loading="lazy"' in source
    assert 'decoding="async"' in source
    assert "srcset" in source


# ---------- image_picker 集成 ---------- #

def test_image_picker_script_loaded(source: str):
    assert "image_upload_client.js" in source
    assert "image_picker.js" in source


def test_image_picker_used_in_both_modals(source: str):
    """新增和编辑 modal 都用 mountImagePicker 单选模式选缩略图。"""
    # mountImagePicker 至少出现两次（create + edit）
    assert source.count("mountImagePicker") >= 2
    assert "mode: 'single'" in source


# ---------- 通用 ---------- #

def test_extends_admin_console_base(source: str):
    assert '{% extends "admin_console/base.html" %}' in source


def test_card_shows_appid_and_cache_status(source: str):
    """卡片副标题展示 appid 和企微缓存状态（运营一眼看出是否能群发）。"""
    assert "appid" in source
    assert "企微缓存" in source
