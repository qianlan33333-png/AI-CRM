"""image_library 模板 contract 测试。

不起 Flask app，直接 grep 模板源码，确保前端 UI 关键结构不被无意撤回：
- 顶部工具栏：上传按钮 + 筛选栏
- 上传走 modal（不再是页面内嵌表单，也不再有 URL tab）
- 卡片精简：缩略图 + 分类 + 标签（不再展示 description 摘要）
- 整张卡片点击进编辑 modal
- 编辑 modal 是大窗左右两栏（左大图 / 右表单 + 只读元信息）
- 启用 / 停用 / 删除操作移到编辑 modal 里
- 删除走硬删 + 引用悬空二次确认（PR-E）

同其他静态模板契约测试风格。
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "image_library.html"


@pytest.fixture(scope="module")
def source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


# ---------- 顶部工具栏 ---------- #

def test_toolbar_has_upload_button(source: str):
    """页面唯一的上传入口是这个按钮，点击打开蒙版。"""
    assert 'id="il-open-upload"' in source
    assert 'class="il-upload-btn"' in source


def test_no_inline_upload_form_left_in_page(source: str):
    """旧版页面顶部那块大表单（il-form / il-form-tabs）已经收敛进 modal，
    不应该还在页面正文里裸出。"""
    assert 'class="il-form"' not in source
    assert 'class="il-form-tabs"' not in source


def test_url_upload_tab_removed(source: str):
    """需求明确去掉外链 URL 上传，只留本地文件。"""
    assert 'data-tab="url"' not in source
    assert 'id="il-url-input"' not in source
    # 也不应该再调 from-url endpoint
    assert "/api/admin/image-library/from-url" not in source


# ---------- 上传 Modal ---------- #

def test_upload_modal_present(source: str):
    assert 'id="il-upload-modal"' in source
    assert 'id="il-upload-file"' in source
    assert 'id="il-upload-name"' in source
    assert 'id="il-upload-category"' in source
    assert 'id="il-upload-description"' in source
    assert 'id="il-upload-tags"' in source


def test_upload_client_script_loaded(source: str):
    """图片上传页必须加载统一上传客户端，避免 nginx HTML 413 被当成 JSON 解析。"""
    assert "image_upload_client.js" in source


def test_upload_modal_submit_passes_metadata_in_multipart(source: str):
    """multipart 上传必须把 description / tags / category 一起 append。"""
    assert "fd.append('image'" in source
    assert "fd.append('description'" in source
    assert "fd.append('tags'" in source
    assert "fd.append('category'" in source
    assert "/api/admin/image-library/upload" in source


def test_upload_modal_prepares_large_image_before_post(source: str):
    """上传前先校验/压缩图片，避免 1MB+ 文件被 nginx 直接 413 成 HTML。"""
    assert "prepareImageForUpload(f)" in source
    assert "prepared.file" in source
    assert "requestJsonWithTimeout || client.requestJson" in source


def test_json_request_helper_falls_back_to_old_upload_client(source: str):
    """静态资源缓存可能让页面拿到旧 image_upload_client.js，模板不能直接崩。"""
    assert "function requestJSON" in source
    assert "client.requestJsonWithTimeout || client.requestJson" in source
    assert "图片上传客户端未加载" in source


def test_upload_modal_has_open_close_handlers(source: str):
    """点 + 上传按钮要能开 modal；点 close / cancel / backdrop / ESC 要能关。"""
    assert 'id="il-upload-close"' in source
    assert 'id="il-upload-cancel"' in source
    assert "openUploadModal" in source
    assert "closeUploadModal" in source


# ---------- 筛选栏 ---------- #

def test_filter_bar_has_keyword_search(source: str):
    assert 'id="il-q"' in source
    assert 'type="search"' in source


def test_filter_panel_has_category_dimension(source: str):
    assert "facetRow('类型', '类型'" in source
    assert 'id="il-category-filter"' not in source


def test_filter_bar_has_only_unlabeled_checkbox(source: str):
    assert 'id="il-only-unlabeled"' in source


def test_filter_bar_has_include_disabled_checkbox(source: str):
    assert 'id="il-include-disabled"' in source


def test_filter_bar_has_dimension_panel(source: str):
    assert 'id="il-facet-panel"' in source
    assert "TAG_DIMENSION_ORDER = ['主题', '特征', '用途', '行业']" in source
    assert "function splitTagDimension" in source
    assert "其他标签" in source


def test_dimension_filter_supports_unlimited_and_multi_select(source: str):
    assert "data-clear=\"true\"" in source
    assert "STATE.filters.tagGroups[dimension]" in source
    assert "delete STATE.filters.tagGroups[dimension]" in source


def test_filter_bar_has_reset_button(source: str):
    assert 'id="il-reset"' in source


def test_category_datalist_shared_at_top_level(source: str):
    """共享一个 datalist#il-category-options，给上传 modal 和编辑 modal 都用。"""
    assert 'id="il-category-options"' in source
    assert "<datalist" in source


# ---------- 网格卡片（精简版）---------- #

def test_card_renders_tags_and_category_chips(source: str):
    """卡片必须展示分类 + 标签 chip。"""
    assert 'class="cat"' in source
    assert 'class="tag"' in source


def test_card_does_not_show_description_summary(source: str):
    """需求：描述移到编辑里，卡片不展示摘要。旧版的 il-card-desc 必须移除。"""
    assert "il-card-desc" not in source


def test_card_marks_unlabeled_records(source: str):
    """description / tags / category 全空时打"未打标"红标。"""
    assert "未打标" in source
    assert 'class="unlabeled"' in source


def test_card_disabled_state_visually_marked(source: str):
    """停用图必须有视觉区分（灰度 + "已停用"角标）。"""
    assert "il-card.disabled" in source
    assert "grayscale" in source
    assert "已停用" in source


def test_card_click_opens_edit_modal(source: str):
    """整张卡片点击进编辑 modal（替代旧版 卡片底部 编辑/停用/删除 按钮）。"""
    assert "card.addEventListener('click'" in source
    assert "openEditModal" in source


def test_card_no_inline_action_buttons(source: str):
    """卡片底部不再有 toggle / delete / edit 按钮（已移到编辑 modal 里）。"""
    assert 'data-action="toggle"' not in source
    assert 'data-action="delete"' not in source
    assert 'data-action="edit"' not in source


# ---------- 编辑 / 详情 Modal（大窗）---------- #

def test_edit_modal_present_with_form_fields(source: str):
    """编辑 modal 必须有 4 个语义字段的输入框。"""
    assert 'id="il-edit-modal"' in source
    assert 'id="il-edit-name"' in source
    assert 'id="il-edit-category"' in source
    assert 'id="il-edit-description"' in source
    assert 'id="il-edit-tags"' in source


def test_edit_modal_has_two_column_layout(source: str):
    """编辑 modal 必须左右两栏：左图 + 右表单。"""
    assert "il-edit-layout" in source
    assert "il-edit-image" in source
    assert "il-edit-meta" in source


def test_edit_modal_shows_large_image(source: str):
    """左栏必须有图片大图容器。"""
    assert 'id="il-edit-image-wrap"' in source
    # 图片是 object-fit:contain（保持比例不裁切）
    assert "object-fit:contain" in source or "object-fit: contain" in source


def test_edit_modal_no_readonly_metadata_block(source: str):
    """编辑 modal 里不应再展示 id / 文件名 / 来源 / MIME / 时间 等只读元信息区。
    标题下方 il-edit-subtitle 一行精简信息已足够。"""
    assert "il-edit-readonly" not in source


def test_edit_modal_has_toggle_button(source: str):
    """启用 / 停用按钮在编辑 modal 里（替代旧版卡片上的 toggle）。"""
    assert 'id="il-edit-toggle"' in source


def test_edit_modal_has_delete_button(source: str):
    """删除按钮在编辑 modal 里（替代旧版卡片上的 delete）。"""
    assert 'id="il-edit-delete"' in source


def test_edit_modal_save_calls_put_endpoint_with_metadata(source: str):
    """保存按钮必须把 name + description + tags + category 一起 PUT。"""
    assert "/api/admin/image-library/" in source
    assert "method: 'PUT'" in source
    assert "description: document.getElementById('il-edit-description').value" in source
    assert "tags: tagsArr" in source
    assert "category: document.getElementById('il-edit-category').value" in source


def test_edit_modal_clickaway_and_esc_close(source: str):
    """点 backdrop / ESC 都要能关 modal。"""
    assert "closeEditModal" in source
    assert "Escape" in source


# ---------- 删除 (PR-E 硬删 + 引用检查) ---------- #

def test_delete_uses_hard_delete_with_force_fallback(source: str):
    """删除按钮：默认硬删 → 有引用返 409 弹二次 confirm → ?force=true 强删。"""
    assert "force=true" in source
    assert "references" in source
    assert "永久删除" in source
    assert "不可恢复" in source


def test_delete_shows_reference_summary_in_second_confirm(source: str):
    """二次 confirm 必须告诉用户被引用了几个 miniprogram / campaign step。"""
    assert "miniprograms" in source
    assert "campaign_steps" in source
    assert "references_cleared" in source


# ---------- API 调用 ---------- #

def test_list_request_supports_filter_query_params(source: str):
    """列表请求必须能传 q / tag_group / category / only_unlabeled，否则筛选失效。"""
    assert "params.set('q'" in source
    assert "params.append('tag_group'" in source
    assert "params.set('category'" in source
    assert "params.set('only_unlabeled'" in source


def test_facets_endpoint_called_on_load(source: str):
    assert "/api/admin/image-library/facets" in source


def test_include_disabled_passes_enabled_only_false(source: str):
    """筛选"含已停用"时必须把 enabled_only=false 传给后端。"""
    assert "enabled_only" in source
    assert "'false'" in source or '"false"' in source


# ---------- 不能误删的老路径 ---------- #

def test_thumbnail_loader_uses_variant_urls(source: str):
    """缩略图加载逻辑使用服务端变体 URL，不再批量拉原图详情。"""
    assert "thumb_320_url" in source
    assert "thumb_160_url" in source
    assert "preview_url" in source
    assert "/api/admin/image-library/' + encodeURIComponent(String(item.id)) + '/thumbnail?size='" in source
    assert "/api/admin/image-library/' + item.id" not in source
    assert "include_data=true" not in source
    assert "source === 'url'" in source


def test_thumbnail_img_uses_responsive_lazy_attrs(source: str):
    assert 'loading="lazy"' in source
    assert 'decoding="async"' in source
    assert "srcset" in source
    assert "sizes=\"180px\"" in source


# ---------- 缩略图懒加载 ---------- #

def test_no_indexeddb_original_base64_cache_left(source: str):
    assert "indexedDB.open" not in source
    assert "data_base64" not in source


def test_thumb_lazy_load_via_intersection_observer(source: str):
    """L1：缩略图懒加载用 IntersectionObserver，视口外的卡片不发请求。"""
    assert "new IntersectionObserver" in source
    assert "rootMargin" in source  # 必须配 rootMargin 提前加载减少滚动 jank
    assert "isIntersecting" in source
    assert "unobserve" in source  # 加载过的卡片要 unobserve，避免重复触发


def test_thumb_observer_disconnect_on_rerender(source: str):
    """重新渲染网格前必须 disconnect 旧 observer，避免内存泄漏 + 多次触发。"""
    assert "thumbObserver" in source
    assert "disconnect" in source


def test_filter_changes_abort_old_list_request(source: str):
    assert "listController" in source
    assert "AbortController" in source
    assert ".abort()" in source


def test_edit_modal_uses_preview_variant(source: str):
    """编辑 modal 加载 preview_720，不用列表卡片图或原图 base64。"""
    assert "thumbnailUrl(item, 'preview')" in source


def test_extends_admin_console_base(source: str):
    """模板必须继承 admin_console/base.html，跟整个后台 shell 一致。"""
    assert '{% extends "admin_console/base.html" %}' in source
