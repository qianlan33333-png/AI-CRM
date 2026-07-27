from __future__ import annotations

from html import escape
import json
from typing import Any
from urllib.parse import quote

from aicrm_next.extensions.commerce.commerce.domain import preview_product
from aicrm_next.extensions.commerce.commerce.repo import build_commerce_repository
from aicrm_next.media_library.application import GetImageVariantQuery
from aicrm_next.platform.shared.errors import ContractError, NotFoundError
from aicrm_next.platform.shared.signed_context import product_context_fragment_bootstrap_script


PUBLIC_PRODUCT_ROUTES = (
    "/p/{path:path}",
    "/pay/{path:path}",
    "/api/products/{path:path}",
    "/api/h5/product-images/{path:path}",
)
PAYMENT_ACTION_SEGMENTS = {"checkout", "payment", "pay", "order", "orders", "jsapi", "notify", "return"}


def route_headers(
    *,
    real_external_call_executed: bool = False,
    payment_request_executed: bool = False,
    order_create_executed: bool = False,
) -> dict[str, str]:
    return {
        "X-AICRM-Route-Owner": "ai_crm_next",
        "X-AICRM-Fallback-Used": "false",
        "X-AICRM-Real-External-Call-Executed": str(bool(real_external_call_executed)).lower(),
        "X-AICRM-Payment-Request-Executed": str(bool(payment_request_executed)).lower(),
        "X-AICRM-Order-Create-Executed": str(bool(order_create_executed)).lower(),
    }


def side_effect_safety() -> dict[str, bool]:
    return {
        "fallback_used": False,
        "real_external_call_executed": False,
        "payment_request_executed": False,
        "order_create_executed": False,
    }


def diagnostics_payload(route: str, *, allowed_methods: list[str]) -> dict[str, Any]:
    return {
        "ok": True,
        "route": route,
        "route_owner": "ai_crm_next",
        "source_status": "next_public_product",
        "allowed_methods": allowed_methods,
        **side_effect_safety(),
    }


def normalize_public_path(path: Any) -> str:
    normalized = str(path or "").strip().strip("/")
    if not normalized or "\\" in normalized or normalized.startswith(".") or "//" in normalized:
        raise NotFoundError("public product path not found")
    return normalized


def payment_action_detected(path: str) -> bool:
    segments = {segment.strip().lower() for segment in str(path or "").split("/") if segment.strip()}
    return bool(segments & PAYMENT_ACTION_SEGMENTS)


def get_public_product(path: Any) -> dict[str, Any]:
    normalized = normalize_public_path(path)
    if payment_action_detected(normalized):
        raise NotFoundError("public product path not found")
    repo = build_commerce_repository()
    product = repo.get_product_by_slug(normalized) or repo.get_product_by_code(normalized)
    if not product or not product.get("enabled"):
        raise NotFoundError("product not found")
    return preview_product(product)


def list_public_products(*, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    repo = build_commerce_repository()
    payload = repo.list_products(limit=limit, offset=offset)
    items = [preview_product(item) for item in payload.get("items") or [] if item.get("enabled")]
    return {
        "ok": True,
        "items": items,
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "route_owner": "ai_crm_next",
        **side_effect_safety(),
    }


def public_product_payload(path: Any) -> dict[str, Any]:
    product = get_public_product(path)
    return {
        "ok": True,
        "product": product,
        "route_owner": "ai_crm_next",
        "source_status": "next_public_product_api",
        "checkout": blocked_checkout_payload(product),
        **side_effect_safety(),
    }


def blocked_checkout_payload(product: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "enabled": False,
        "status": "blocked",
        "message": "Public checkout/payment execution is blocked in this Legacy Exit group.",
        "next_route": f"/p/{product.get('product_code')}" if product else "",
        **side_effect_safety(),
    }


def blocked_action_payload(path: Any, *, method: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "public_product_payment_action_blocked",
        "error_code": "public_product_payment_action_blocked",
        "message": "This public product route only serves display/read contracts; checkout, payment, and order creation are out of scope.",
        "path": normalize_public_path(path),
        "method": method.upper(),
        "route_owner": "ai_crm_next",
        "source_status": "blocked_public_product_action",
        **side_effect_safety(),
    }


def product_not_found_payload(path: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "product_not_found",
        "error_code": "product_not_found",
        "message": "Public product path is not configured.",
        "path": str(path or "").strip().strip("/"),
        "route_owner": "ai_crm_next",
        **side_effect_safety(),
    }


def _positive_int_text(value: Any) -> str:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return ""
    return str(normalized) if normalized > 0 else ""


def _product_slice_image_ids(product: dict[str, Any]) -> set[str]:
    image_ids: set[str] = set()
    for item in list(product.get("slices") or []):
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        image_id = _positive_int_text(item.get("image_library_id") or item.get("library_image_id") or item.get("asset_id"))
        if image_id:
            image_ids.add(image_id)
    return image_ids


def public_product_image_variant(product_code: Any, image_id: Any, variant_key: Any) -> dict[str, Any]:
    product = get_public_product(product_code)
    normalized_image_id = _positive_int_text(image_id)
    if not normalized_image_id or normalized_image_id not in _product_slice_image_ids(product):
        raise NotFoundError("public product image not found")
    try:
        payload = GetImageVariantQuery()(normalized_image_id, str(variant_key or "").strip())
    except (ContractError, NotFoundError, TypeError, ValueError) as exc:
        raise NotFoundError("public product image not found") from exc
    variant = payload.get("variant") if isinstance(payload, dict) else None
    if not isinstance(variant, dict):
        raise NotFoundError("public product image not found")
    return variant


def render_product_page(product: dict[str, Any], *, context_status: str = "") -> str:
    title = escape(str(product.get("title") or "商品详情"))
    cta = escape(str(product.get("buy_button_text") or product.get("cta_text") or "立即报名"))
    checkout_url = escape(f"/pay/{product.get('product_code') or ''}", quote=True)
    media = _render_detail_media(product)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>{title} · 商品详情</title>
  {_product_page_styles()}
</head>
<body class="product-body">
  <nav class="sticky-buy" aria-label="商品操作">
    <div>
      <div class="sticky-title">{title}</div>
      <div class="sticky-price"><small>¥</small>{_price_amount(product)}</div>
    </div>
    <a class="cta" href="{checkout_url}" data-context-status="{escape(str(context_status or 'missing'), quote=True)}">{cta}</a>
  </nav>
  <main class="product-page" data-route-owner="ai_crm_next" data-fallback-used="false">
    {media}
  </main>
  {product_context_fragment_bootstrap_script()}
</body>
</html>"""


def render_lead_qr_modal() -> str:
    return """
  <div class="qr-modal" id="leadQrModal" aria-hidden="true">
    <div class="qr-panel" role="dialog" aria-modal="true" aria-labelledby="leadQrModalTitle">
      <div class="modal-title" id="leadQrModalTitle">报名成功</div>
      <div class="modal-desc">扫码添加企微领取后续资料</div>
      <img class="qr-img" id="leadQrImage" alt="报名后企微渠道二维码">
      <button class="close-button" id="closeQrButton" type="button">关闭</button>
    </div>
  </div>"""


def lead_qr_modal_styles() -> str:
    return """
    .qr-modal {
      position: fixed; inset: 0; background: rgba(16, 32, 58, .48); backdrop-filter: blur(4px);
      z-index: 40; display: none; align-items: center; justify-content: center; padding: 22px;
    }
    .qr-modal.show { display: flex; }
    .qr-panel {
      width: min(100%, 360px); border-radius: 8px; background: #fff; padding: 26px 22px;
      text-align: center; box-shadow: 0 20px 60px rgba(16, 32, 58, .25);
    }
    .qr-img {
      width: min(258px, 100%); aspect-ratio: 1; margin: 18px auto; border-radius: 8px;
      border: 1px solid #dae4f3; padding: 12px; object-fit: contain; display: block; background: #fff;
    }
    .modal-title { color: var(--text, #10203a); font-size: 24px; font-weight: 950; }
    .modal-desc { color: var(--muted, #687891); font-size: 15px; }
    .close-button {
      width: 100%; height: 42px; border: 0; border-radius: 8px; background: var(--blue, #2f6df6); color: #fff;
      font: inherit; font-weight: 900; margin-top: 14px; cursor: pointer;
    }
    """


def lead_qr_modal_controller_script() -> str:
    return """<script>
    (function () {
      window.createLeadQrModalController = function (options) {
        const modal = options && options.modal;
        const image = options && options.image;
        const closeButton = options && options.closeButton;

        function close() {
          if (!modal) return;
          modal.classList.remove("show");
          modal.setAttribute("aria-hidden", "true");
        }

        function clear() {
          close();
          if (image) image.removeAttribute("src");
        }

        function open(source) {
          const normalizedSource = String(source || "").trim();
          if (!normalizedSource || !modal || !image) return;
          image.src = normalizedSource;
          modal.classList.add("show");
          modal.setAttribute("aria-hidden", "false");
        }

        if (closeButton) closeButton.addEventListener("click", close);
        if (modal) {
          modal.addEventListener("click", function (event) {
            if (event.target === modal) close();
          });
        }
        document.addEventListener("keydown", function (event) {
          if (event.key === "Escape" && modal && modal.getAttribute("aria-hidden") === "false") close();
        });

        return { open: open, close: close, clear: clear };
      };
    })();
  </script>"""


def render_pay_landing(product: dict[str, Any], page_state: dict[str, Any]) -> str:
    title = escape(str(product.get("title") or "支付入口"))
    state_json = json.dumps(page_state, ensure_ascii=False).replace("</", "<\\/")
    identity_ready = bool(page_state.get("identity_ready"))
    paid_order = page_state.get("paid_order")
    show_mobile_input = bool(identity_ready and page_state.get("require_mobile") and not paid_order)
    cta_text = escape(str(page_state.get("cta_text") or product.get("buy_button_text") or "立即报名"))
    require_mobile_html = (
        """
      <div class="pay-mobile" id="mobileBlock">
        <label class="pay-label" for="mobileInput">手机号</label>
        <input id="mobileInput" inputmode="numeric" maxlength="11" placeholder="请输入手机号">
        <div class="error-text" id="mobileError"></div>
      </div>"""
        if show_mobile_input
        else ""
    )
    if paid_order:
        action_html = ""
    elif not identity_ready:
        action_html = (
            f'<a class="button-link" href="{escape(str(page_state.get("oauth_start_url") or ""), quote=True)}">微信授权后继续</a>'
            if page_state.get("is_wechat_browser")
            else '<button class="pay-action" type="button" disabled>请在微信中打开</button>'
        )
    else:
        action_html = f'<button id="payButton" class="pay-action" type="button" {"disabled" if not page_state.get("enabled") else ""}>{cta_text}</button>'
    completion_action = page_state.get("completion_action") if isinstance(page_state.get("completion_action"), dict) else {}
    qr_button_html = (
        ""
        if completion_action.get("type") == "redirect"
        else '<button class="qr-reopen-button" id="showLeadQrButton" type="button">查看领取二维码</button>'
    )
    if paid_order:
        state_message = "已报名，正在打开报名成功页。"
    elif not page_state.get("enabled"):
        state_message = "当前支付暂未启用。"
    elif not identity_ready:
        state_message = str(page_state.get("identity_message") or "需要先完成微信授权。")
    else:
        state_message = "已就绪。"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>{title} · 支付入口</title>
  {_pay_page_styles()}
</head>
<body class="product-body">
  <main class="pay-page" data-route-owner="ai_crm_next" data-fallback-used="false">
    <section class="pay-card" id="checkoutCard">
      <div class="pay-title">确认报名信息</div>
      <div class="pay-row">
        <span class="pay-label">购买商品</span>
        <span class="pay-value">{title}</span>
      </div>
      <div class="pay-row">
        <span class="pay-label">商品原价</span>
        <span class="pay-value">¥{_price_amount(product)}</span>
      </div>
      <div class="coupon-block" id="couponBlock" hidden>
        <div class="coupon-head"><strong>优惠券</strong><span class="coupon-count" id="couponCount"></span></div>
        <select class="coupon-select" id="couponSelect" aria-label="选择优惠券"></select>
        <div class="coupon-hint" id="couponHint"></div>
      </div>
      <div class="pay-row discount-row" id="discountRow" hidden>
        <span class="pay-label">优惠金额</span>
        <span class="pay-value discount-value" id="discountAmount">-¥0.00</span>
      </div>
      <div class="pay-row">
        <span class="pay-label">支付金额</span>
        <span class="pay-value" id="finalAmount">¥{_price_amount(product)}</span>
      </div>
      {require_mobile_html}
      <div id="payState" class="state-line">{state_message}</div>
      {action_html}
    </section>

    <section class="pay-card success-box" id="successBox">
      <div class="success-tick">✓</div>
      <div class="success-title">支付成功</div>
      <div class="success-desc" id="successDesc">报名成功</div>
      <div id="weappLaunchPanel" class="weapp-launch-panel" hidden>
        <div class="weapp-launch-title">正在打开小程序</div>
        <div class="weapp-launch-desc" id="weappLaunchDesc">请点击下方按钮继续。</div>
        <div id="weappLaunchHost"></div>
        <a id="weappFallbackLink" class="fallback-link" href="#">无法打开？点击备用链接</a>
      </div>
      {qr_button_html}
    </section>
  </main>

  {render_lead_qr_modal()}
  {lead_qr_modal_controller_script()}
  {_pay_page_script(state_json)}
  {product_context_fragment_bootstrap_script()}
</body>
</html>"""


def render_not_found_page(path: Any) -> str:
    normalized = escape(str(path or "").strip().strip("/") or "-")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>商品不存在</title>
</head>
<body>
  <main data-route-owner="ai_crm_next" data-fallback-used="false">
    <h1>商品不存在</h1>
    <p>路径 {normalized} 未配置公开商品。</p>
  </main>
</body>
</html>"""


def _detail_image_source(item: Any, product_code: str = "") -> str:
    if isinstance(item, str):
        source = item.strip()
        return "" if source.lower().startswith("data:image/") else source
    if isinstance(item, dict):
        image_id = _positive_int_text(item.get("image_library_id") or item.get("library_image_id") or item.get("asset_id"))
        if image_id and product_code:
            return f"/api/h5/product-images/{quote(product_code, safe='')}/{quote(image_id, safe='')}/variants/original"
        for key in ("image_url", "data_url", "url", "src"):
            value = str(item.get(key) or "").strip()
            if value:
                if value.lower().startswith("data:image/"):
                    return ""
                return value
    return ""


def _detail_image_items(product: dict[str, Any]) -> list[dict[str, Any]]:
    product_code = str(product.get("product_code") or "").strip()
    items: list[dict[str, Any]] = []
    for group_key in ("slices", "detail_images"):
        for item in list(product.get(group_key) or []):
            source = _detail_image_source(item, product_code)
            if source:
                width = _positive_int_text(item.get("width") if isinstance(item, dict) else None)
                height = _positive_int_text(item.get("height") if isinstance(item, dict) else None)
                items.append({"source": source, "width": width, "height": height})
    return items


def _render_detail_media(product: dict[str, Any]) -> str:
    items = _detail_image_items(product)
    if not items:
        return ""
    image_tags: list[str] = []
    for index, item in enumerate(items):
        loading = "eager" if index == 0 else "lazy"
        fetchpriority = "high" if index == 0 else "low"
        size_attrs = ""
        if item.get("width") and item.get("height"):
            size_attrs = f' width="{escape(item["width"], quote=True)}" height="{escape(item["height"], quote=True)}"'
        image_tags.append(
            f'      <img class="slice-img" src="{escape(item["source"], quote=True)}" loading="{loading}" decoding="async" fetchpriority="{fetchpriority}"{size_attrs} alt="">'
        )
    images = "\n".join(image_tags)
    return f"""
    <section class="detail-media" aria-label="商品详情图">
{images}
    </section>"""


def _render_detail_sections(product: dict[str, Any]) -> str:
    sections = []
    for item in list(product.get("detail_sections") or []):
        if not isinstance(item, dict):
            continue
        title = escape(str(item.get("title") or "").strip())
        body = escape(str(item.get("body") or item.get("content") or "").strip())
        if not title and not body:
            continue
        sections.append(
            f"""
      <article class="detail-card">
        {f"<h2>{title}</h2>" if title else ""}
        {f"<p>{body}</p>" if body else ""}
      </article>"""
        )
    if not sections:
        sections.append(
            """
      <article class="detail-card">
        <h2>服务说明</h2>
        <p>报名后请按页面指引完成后续联系与权益开通。</p>
      </article>"""
        )
    return "\n".join(["    <section class=\"detail-section\">", *sections, "    </section>"])


def _price_amount(product: dict[str, Any]) -> str:
    cents = int(product.get("price_cents") or 0)
    return f"{cents / 100:.2f}"


def format_price(product: dict[str, Any]) -> str:
    cents = int(product.get("price_cents") or 0)
    currency = str(product.get("currency") or "CNY").strip() or "CNY"
    return f"{currency} {cents / 100:.2f}"


def _product_page_styles() -> str:
    return """<style>
    :root { --text: #10203a; --line: #e4eaf4; --cta: #f4b345; --cta-deep: #d88700; }
    * { box-sizing: border-box; }
    html, body { margin: 0; min-height: 100%; background: #fff; color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif; letter-spacing: 0; }
    .product-page { width: min(100%, 750px); min-height: 100vh; margin: 0 auto; background: #fff; padding-bottom: 88px; }
    .detail-media { background: #fff; }
    .slice-img { width: 100%; display: block; background: #fff; min-height: 80px; object-fit: cover; }
    .sticky-buy {
      position: fixed; left: 50%; bottom: 0; transform: translateX(-50%); z-index: 20;
      width: min(100%, 750px); padding: 10px 14px 12px; background: rgba(255, 253, 248, .96);
      backdrop-filter: blur(12px); border-top: 1px solid rgba(236, 217, 184, .86);
      box-shadow: 0 -10px 34px rgba(26, 32, 48, .12);
      display: grid; grid-template-columns: minmax(0, 1fr) 124px; gap: 12px; align-items: center;
    }
    .sticky-title { font-size: 12px; font-weight: 900; color: #5a5146; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .sticky-price { font-size: 22px; font-weight: 950; color: #c56e00; letter-spacing: 0; }
    .sticky-price small { font-size: 12px; margin-right: 2px; }
    .cta {
      height: 52px; border: 0; border-radius: 999px; background: var(--cta); color: #2a1c07;
      font-weight: 950; font-size: 15px; box-shadow: 0 8px 18px rgba(244, 179, 69, .34);
      display: inline-flex; align-items: center; justify-content: center; text-decoration: none;
    }
  </style>"""


def _pay_page_styles() -> str:
    return """<style>
    :root {
      color-scheme: light;
      --bg: #f7f9fe;
      --panel: #ffffff;
      --line: #e4eaf4;
      --text: #10203a;
      --muted: #687891;
      --blue: #2f6df6;
      --green: #128a45;
      --green-bg: #e9f8ef;
      --red: #e23030;
      --shadow: 0 18px 50px rgba(23, 42, 76, .08);
    }
    * { box-sizing: border-box; }
    body.product-body {
      margin: 0; min-height: 100vh; background: var(--bg); color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      letter-spacing: 0;
    }
    .pay-page { width: min(100%, 520px); min-height: 100vh; margin: 0 auto; padding: 72px 18px 110px; }
    .pay-card { border: 1px solid var(--line); border-radius: 8px; background: #fff; box-shadow: var(--shadow); padding: 20px; }
    .pay-title { font-size: 21px; font-weight: 950; margin-bottom: 14px; letter-spacing: 0; }
    .pay-row { display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px dashed var(--line); padding: 12px 0; }
    .pay-label { color: var(--muted); font-weight: 800; }
    .pay-value { font-weight: 900; text-align: right; overflow-wrap: anywhere; }
    .pay-mobile { margin-top: 18px; }
    .pay-mobile label { display: block; margin-bottom: 7px; }
    .pay-mobile input { width: 100%; height: 48px; border: 1px solid var(--line); border-radius: 8px; padding: 0 13px; outline: none; font: inherit; }
    .pay-mobile input:focus { border-color: #9bb8ff; box-shadow: 0 0 0 3px rgba(47, 109, 246, .12); }
    .coupon-block { margin-top: 14px; padding: 14px; border: 1px solid #dce7ff; border-radius: 8px; background: #f7faff; }
    .coupon-block[hidden], .discount-row[hidden] { display: none; }
    .coupon-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
    .coupon-head strong { font-size: 14px; }
    .coupon-count { color: var(--blue); font-size: 12px; font-weight: 800; }
    .coupon-select { width: 100%; min-height: 42px; border: 1px solid #cbd9f8; border-radius: 8px; background: #fff; color: var(--text); padding: 0 10px; font: inherit; }
    .coupon-hint { min-height: 18px; margin-top: 7px; color: var(--muted); font-size: 12px; }
    .discount-value { color: #e5484d; }
    .state-line { min-height: 22px; margin-top: 14px; color: var(--muted); font-size: 13px; line-height: 1.6; }
    .state-line.error { color: var(--red); }
    .state-line.success { color: var(--green); }
    .error-text { font-size: 12px; color: var(--red); margin-top: 6px; min-height: 18px; }
    .pay-action, .button-link, .qr-reopen-button {
      width: 100%; height: 52px; border: 0; border-radius: 8px; background: var(--blue); color: #fff;
      font: inherit; font-weight: 950; margin-top: 12px; cursor: pointer; text-decoration: none;
      display: inline-flex; align-items: center; justify-content: center;
    }
    .qr-reopen-button {
      display: none; height: 46px; background: #edf3ff; color: var(--blue);
      border: 1px solid #cddcff; box-shadow: none; font-weight: 900;
    }
    .qr-reopen-button.show { display: inline-flex; }
    .pay-action[disabled] { opacity: .55; cursor: default; }
    .success-box { display: none; text-align: center; padding-top: 6px; margin-top: 14px; }
    .success-box.show { display: block; }
    .success-tick { width: 66px; height: 66px; border-radius: 50%; background: var(--green-bg); color: var(--green); display: flex; align-items: center; justify-content: center; font-size: 34px; font-weight: 900; margin: 18px auto; }
    .success-title { font-size: 22px; font-weight: 950; }
    .success-desc { color: var(--muted); margin: 8px 0 0; }
    .weapp-launch-panel[hidden] { display: none; }
    .weapp-launch-panel {
      display: grid; gap: 10px; margin-top: 16px; padding: 14px;
      border: 1px solid var(--line); border-radius: 8px; background: #fff;
      text-align: left;
    }
    .weapp-launch-title { color: var(--text); font-size: 15px; font-weight: 900; }
    .weapp-launch-desc { color: var(--muted); font-size: 13px; line-height: 1.6; }
    .fallback-link { display: inline-flex; color: #3370ff; font-size: 14px; font-weight: 800; text-decoration: none; }
    """ + lead_qr_modal_styles() + """
  </style>"""


def _pay_page_script(state_json: str) -> str:
    return f"""<script>
    (function () {{
      const state = {state_json};
      const payButton = document.getElementById("payButton");
      const stateLine = document.getElementById("payState");
      const mobileInput = document.getElementById("mobileInput");
      const mobileError = document.getElementById("mobileError");
      const couponBlock = document.getElementById("couponBlock");
      const couponSelect = document.getElementById("couponSelect");
      const couponCount = document.getElementById("couponCount");
      const couponHint = document.getElementById("couponHint");
      const discountRow = document.getElementById("discountRow");
      const discountAmount = document.getElementById("discountAmount");
      const finalAmount = document.getElementById("finalAmount");
      const checkoutCard = document.getElementById("checkoutCard");
      const successBox = document.getElementById("successBox");
      const successDesc = document.getElementById("successDesc");
      const showLeadQrButton = document.getElementById("showLeadQrButton");
      const leadQrController = window.createLeadQrModalController ? window.createLeadQrModalController({{
        modal: document.getElementById("leadQrModal"),
        image: document.getElementById("leadQrImage"),
        closeButton: document.getElementById("closeQrButton")
      }}) : null;
      const weappLaunchPanel = document.getElementById("weappLaunchPanel");
      const weappLaunchHost = document.getElementById("weappLaunchHost");
      const weappLaunchDesc = document.getElementById("weappLaunchDesc");
      const weappFallbackLink = document.getElementById("weappFallbackLink");
      let activeOrderNo = "";
      let paidOrder = state.paid_order || null;
      let availableCoupons = [];
      let couponLoadFailed = false;
      const clientOrderStorageKey = `aicrm-checkout-ref:${{state.create_order_url}}:${{state.product.product_code}}`;
      let storedClientOrderRef = "";
      try {{ storedClientOrderRef = sessionStorage.getItem(clientOrderStorageKey) || ""; }} catch (_error) {{}}
      const clientOrderRef = storedClientOrderRef || ((globalThis.crypto && crypto.randomUUID)
        ? crypto.randomUUID()
        : `checkout-${{Date.now()}}-${{Math.random().toString(16).slice(2)}}`);
      try {{ sessionStorage.setItem(clientOrderStorageKey, clientOrderRef); }} catch (_error) {{}}

      function clearCompletedClientOrderRef() {{
        try {{
          if (sessionStorage.getItem(clientOrderStorageKey) === clientOrderRef) {{
            sessionStorage.removeItem(clientOrderStorageKey);
          }}
        }} catch (_error) {{}}
      }}

      function money(cents) {{
        return `¥${{(Number(cents || 0) / 100).toFixed(2)}}`;
      }}

      function selectedCoupon() {{
        if (!couponSelect || !availableCoupons.length) return null;
        const value = String(couponSelect.value || "auto");
        if (value === "none") return null;
        if (value === "auto") return availableCoupons[0] || null;
        return availableCoupons.find((item) => String(item.claim_no || "") === value) || null;
      }}

      function couponChoice() {{
        if (!couponSelect || !availableCoupons.length) return {{ mode: "none" }};
        const value = String(couponSelect.value || "auto");
        if (value === "none") return {{ mode: "none" }};
        if (value === "auto") return {{ mode: "auto" }};
        return {{ mode: "claim", claim_no: value }};
      }}

      function renderCouponAmount() {{
        const coupon = selectedCoupon();
        const subtotal = Number((state.product && state.product.amount_total) || 0);
        const discount = coupon ? Number(coupon.discount_amount_total || 0) : 0;
        if (discountRow) discountRow.hidden = discount <= 0;
        if (discountAmount) discountAmount.textContent = `-${{money(discount)}}`;
        if (finalAmount) finalAmount.textContent = money(Math.max(0, subtotal - discount));
        if (couponHint) couponHint.textContent = coupon
          ? `${{coupon.name || "优惠券"}}，本单预计减免 ${{money(discount)}}`
          : "本单不使用优惠券";
      }}

      async function loadCoupons() {{
        if (!state.identity_ready || !state.available_coupon_url || paidOrder) return;
        try {{
          const response = await fetch(state.available_coupon_url, {{ credentials: "same-origin" }});
          const payload = await response.json();
          if (!response.ok || payload.ok === false) throw new Error(payload.error || "优惠券加载失败");
          availableCoupons = Array.isArray(payload.items) ? payload.items : [];
          if (!availableCoupons.length || !couponBlock || !couponSelect) return;
          couponBlock.hidden = false;
          if (couponCount) couponCount.textContent = `${{availableCoupons.length}} 张可用`;
          couponSelect.innerHTML = "";
          const autoOption = document.createElement("option");
          autoOption.value = "auto";
          autoOption.textContent = "自动选择最优惠";
          couponSelect.appendChild(autoOption);
          availableCoupons.forEach((item) => {{
            const option = document.createElement("option");
            option.value = String(item.claim_no || "");
            option.textContent = `${{item.name || "优惠券"}}（减 ${{money(item.discount_amount_total)}}）`;
            couponSelect.appendChild(option);
          }});
          const noneOption = document.createElement("option");
          noneOption.value = "none";
          noneOption.textContent = "不使用优惠券";
          couponSelect.appendChild(noneOption);
          couponSelect.addEventListener("change", renderCouponAmount);
          renderCouponAmount();
        }} catch (error) {{
          couponLoadFailed = true;
          if (couponHint) couponHint.textContent = error && error.message ? error.message : "优惠券加载失败";
        }}
      }}

      function setState(message, type) {{
        if (!stateLine) return;
        stateLine.textContent = message;
        stateLine.classList.remove("error", "success");
        if (type) stateLine.classList.add(type);
      }}

      function setMobileError(message) {{
        if (mobileError) mobileError.textContent = message || "";
      }}

      function normalizedMobile() {{
        return String((mobileInput && mobileInput.value) || "").replace(/\\D+/g, "");
      }}

      function validateMobile() {{
        if (!state.require_mobile) return "";
        const value = normalizedMobile();
        if (!/^1[3-9]\\d{{9}}$/.test(value)) {{
          setMobileError("请填写 11 位手机号后再继续。");
          return "";
        }}
        setMobileError("");
        return value;
      }}

      function invokeBridge(payParams) {{
        return new Promise((resolve) => {{
          const call = function () {{
            window.WeixinJSBridge.invoke("getBrandWCPayRequest", payParams, function (res) {{
              resolve(res || {{}});
            }});
          }};
          if (typeof window.WeixinJSBridge === "undefined") {{
            document.addEventListener("WeixinJSBridgeReady", call, false);
          }} else {{
            call();
          }}
        }});
      }}

      async function confirmStatus(outTradeNo, refresh) {{
        const url = state.status_url_template.replace("{{out_trade_no}}", encodeURIComponent(outTradeNo)) + (refresh ? "?refresh=1" : "");
        const response = await fetch(url, {{ credentials: "same-origin" }});
        const payload = await response.json();
        if (!response.ok || !payload.ok) {{
          throw new Error(payload.error || "支付结果确认失败");
        }}
        return payload.order || {{}};
      }}

      function delay(ms) {{
        return new Promise((resolve) => window.setTimeout(resolve, ms));
      }}

      async function waitForPaid(outTradeNo) {{
        for (let index = 0; index < 8; index += 1) {{
          const order = await confirmStatus(outTradeNo, index === 0);
          if (order.status === "paid") return order;
          await delay(900);
        }}
        return confirmStatus(outTradeNo, true);
      }}

      async function createOrder() {{
        const body = {{
          product_code: state.product.product_code,
          order_source: "product_checkout",
          client_order_ref: clientOrderRef,
          coupon_choice: couponChoice()
        }};
        if (state.require_mobile) {{
          const mobile = validateMobile();
          if (!mobile) return null;
          body.mobile = mobile;
        }}
        const response = await fetch(state.create_order_url, {{
          method: "POST",
          credentials: "same-origin",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(body)
        }});
        const payload = await response.json();
        if (!response.ok || !payload.ok) {{
          if (payload.oauth_start_url) {{
            window.location.href = payload.oauth_start_url;
            return null;
          }}
          const friendlyErrors = {{
            identity_resolution_required: "微信身份正在同步，请稍后重试。",
            identity_conflict: "当前微信身份存在冲突，请联系客服处理。",
            coupon_unavailable: "所选优惠券状态已变化，请刷新后重新选择。",
            coupon_amount_invalid: "商品价格已变化，当前优惠券无法使用。",
            order_reconciliation_pending: "订单状态正在与微信确认，请勿重复下单，稍后刷新查看。",
            wechat_pay_provider_outcome_unknown: "微信支付订单状态确认中，请勿重复下单，稍后刷新查看。"
          }};
          throw new Error(friendlyErrors[payload.error] || payload.error || "下单失败");
        }}
        return payload;
      }}

      function isSafeRedirectUrl(url) {{
        return /^https:\\/\\//.test(url) || /^\\/(?!\\/)[^\\s\\\\]*$/.test(url);
      }}

      function postPaidRedirectUrl() {{
        const url = String(state.post_paid_redirect_url || "");
        return url && isSafeRedirectUrl(url) ? url : "";
      }}

      function escapeAttr(value) {{
        return String(value || "")
          .replace(/&/g, "&amp;")
          .replace(/"/g, "&quot;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
      }}

      function fallbackUrlFromTarget(target) {{
        if (!target || !target.enabled) return "";
        const link = target.url_link || {{}};
        return String(target.fallback_url || target.h5_url || link.url || "");
      }}

      function dynamicUrlLinkResolverUrl(target, fallbackUrl) {{
        const link = target && target.url_link ? target.url_link : {{}};
        const sourceUrl = String(link.source_url || "").trim();
        if (!/^https:\\/\\/[^\\s\\\\]+$/i.test(sourceUrl)) return "";
        const params = new URLSearchParams();
        params.set("source_url", sourceUrl);
        params.set("response_url_key", String(link.response_url_key || "url_link"));
        const candidateFallback = String(fallbackUrl || fallbackUrlFromTarget(target) || "").trim();
        if (candidateFallback && isSafeRedirectUrl(candidateFallback)) params.set("fallback_url", candidateFallback);
        return "/api/h5/navigation-target/url-link/resolve?" + params.toString();
      }}

      function miniProgramActionFromTarget(target, order) {{
        if (!target || !target.enabled || target.target_type !== "mini_program") return null;
        const mini = target.mini_program || {{}};
        const fallbackUrl = fallbackUrlFromTarget(target) || String((order && order.success_url) || "");
        if (!mini.path || (!mini.username && !mini.appid)) {{
          return fallbackUrl && isSafeRedirectUrl(fallbackUrl) ? {{ type: "redirect", redirect_url: fallbackUrl }} : null;
        }}
        return {{ type: "mini_program", navigation_target: target, fallback_url: fallbackUrl }};
      }}

      function urlLinkActionFromTarget(target, order) {{
        if (!target || !target.enabled || target.target_type !== "url_link") return null;
        const link = target.url_link || {{}};
        const fallbackUrl = fallbackUrlFromTarget(target) || String((order && order.success_url) || "");
        if (link.source_url) return {{ type: "url_link", navigation_target: target, fallback_url: fallbackUrl }};
        if (link.url && isSafeRedirectUrl(link.url)) return {{ type: "redirect", redirect_url: link.url }};
        return fallbackUrl && isSafeRedirectUrl(fallbackUrl) ? {{ type: "redirect", redirect_url: fallbackUrl }} : null;
      }}

      function completionActionFromOrder(order) {{
        const action = order && order.completion_action ? order.completion_action : {{}};
        if (action && action.type === "mini_program" && action.navigation_target) {{
          const miniAction = miniProgramActionFromTarget(action.navigation_target, order);
          if (miniAction) return miniAction;
        }}
        if (action && action.type === "url_link" && action.navigation_target) {{
          const linkAction = urlLinkActionFromTarget(action.navigation_target, order);
          if (linkAction) return linkAction;
        }}
        const target = order && order.completion_target ? order.completion_target : {{}};
        const linkAction = urlLinkActionFromTarget(target, order);
        if (linkAction) return linkAction;
        const miniAction = miniProgramActionFromTarget(target, order);
        if (miniAction) return miniAction;
        const actionUrl = String((action && action.redirect_url) || "");
        if (action && action.type === "redirect" && actionUrl && isSafeRedirectUrl(actionUrl)) {{
          return {{ type: "redirect", redirect_url: actionUrl }};
        }}
        const configured = order && order.completion_redirect ? order.completion_redirect : {{}};
        const fallbackUrl = String((configured && configured.url) || (order && order.completion_redirect_url) || "");
        const fallbackEnabled = Boolean((configured && configured.enabled) || (order && order.completion_redirect_enabled));
        if (fallbackEnabled && fallbackUrl && isSafeRedirectUrl(fallbackUrl)) {{
          return {{ type: "redirect", redirect_url: fallbackUrl }};
        }}
        return {{ type: "default", redirect_url: "" }};
      }}

      function openMiniProgram(action) {{
        const target = (action && action.navigation_target) || {{}};
        const mini = target.mini_program || {{}};
        const fallbackUrl = String(action.fallback_url || fallbackUrlFromTarget(target) || "");
        const safeFallback = fallbackUrl && isSafeRedirectUrl(fallbackUrl) ? fallbackUrl : "#";
        if (weappLaunchPanel) weappLaunchPanel.hidden = false;
        if (weappLaunchHost) weappLaunchHost.innerHTML = "";
        if (weappFallbackLink) weappFallbackLink.href = safeFallback;
        if (weappLaunchDesc) weappLaunchDesc.textContent = "请点击下方按钮继续。";
        if (!/MicroMessenger/i.test(navigator.userAgent || "") || typeof customElements === "undefined" || !customElements.get("wx-open-launch-weapp") || !mini.username || !mini.path) {{
          if (weappLaunchDesc) weappLaunchDesc.textContent = "无法直接打开，点击备用链接。";
          return;
        }}
        const path = String(mini.path || "/") + (mini.query ? "?" + mini.query : "");
        if (weappLaunchHost) {{
          weappLaunchHost.innerHTML = `
            <wx-open-launch-weapp
              id="launch-weapp"
              username="${{escapeAttr(mini.username)}}"
              path="${{escapeAttr(path)}}">
              <template>
                <style>
                  .weapp-launch-button {{
                    width: 100%;
                    height: 48px;
                    border: 0;
                    border-radius: 8px;
                    background: #3370ff;
                    color: #fff;
                    font-size: 16px;
                    font-weight: 800;
                  }}
                </style>
                <button class="weapp-launch-button">打开小程序</button>
              </template>
            </wx-open-launch-weapp>
          `;
          const launcher = weappLaunchHost.querySelector("#launch-weapp");
          if (launcher) {{
            launcher.addEventListener("error", function() {{
              if (weappLaunchDesc) weappLaunchDesc.textContent = "无法直接打开，点击备用链接。";
            }});
            launcher.addEventListener("launch", function() {{ setState("正在打开小程序...", "success"); }});
          }}
        }}
      }}

      function leadQrFromOrder(order) {{
        if (completionActionFromOrder(order).type === "redirect") return {{}};
        const leadQr = order && order.lead_qr ? order.lead_qr : {{}};
        return leadQr && leadQr.qr_url ? leadQr : {{}};
      }}

      function showLeadQr(order) {{
        const leadQr = leadQrFromOrder(order);
        if (!leadQr.qr_url || !leadQrController) return;
        leadQrController.open(leadQr.qr_url);
      }}

      function syncLeadQrButton(order) {{
        if (!showLeadQrButton) return;
        showLeadQrButton.classList.toggle("show", Boolean(leadQrFromOrder(order).qr_url));
      }}

      function showPaid(order, options) {{
        const autoShowQr = !options || options.autoShowQr !== false;
        paidOrder = order || paidOrder || {{}};
        clearCompletedClientOrderRef();
        const completionAction = completionActionFromOrder(paidOrder);
        if (completionAction.type === "url_link") {{
          const resolverUrl = dynamicUrlLinkResolverUrl(completionAction.navigation_target, completionAction.fallback_url);
          if (resolverUrl) {{
            setState("报名成功，正在打开小程序...", "success");
            window.location.href = resolverUrl;
            return;
          }}
        }}
        if (completionAction.type === "mini_program") {{
          setState("报名成功，正在打开小程序...", "success");
          if (checkoutCard) checkoutCard.style.display = "none";
          if (successBox) successBox.classList.add("show");
          if (successDesc) {{
            successDesc.textContent = "已购买 " + state.product.name + "，支付金额 ¥" + (Number((paidOrder && paidOrder.amount_total) || state.product.amount_total || 0) / 100).toFixed(2) + "。";
          }}
          openMiniProgram(completionAction);
          return;
        }}
        if (completionAction.type === "redirect" && completionAction.redirect_url) {{
          setState("报名成功，正在跳转...", "success");
          window.location.href = completionAction.redirect_url;
          return;
        }}
        const servicePeriodRedirect = postPaidRedirectUrl();
        if (servicePeriodRedirect && !leadQrFromOrder(paidOrder).qr_url) {{
          setState("支付成功，正在刷新服务期...", "success");
          window.location.href = servicePeriodRedirect;
          return;
        }}
        if (checkoutCard) checkoutCard.style.display = "none";
        if (successBox) successBox.classList.add("show");
        if (successDesc) {{
          successDesc.textContent = "已购买 " + state.product.name + "，支付金额 ¥" + (Number((paidOrder && paidOrder.amount_total) || state.product.amount_total || 0) / 100).toFixed(2) + "。";
        }}
        syncLeadQrButton(paidOrder);
        if (autoShowQr) showLeadQr(paidOrder);
      }}

      if (state.paid_order) {{
        showPaid(state.paid_order, {{ autoShowQr: false }});
      }}
      const couponLoadPromise = loadCoupons();

      if (payButton) {{
        payButton.addEventListener("click", async function () {{
          if (state.require_mobile && !validateMobile()) return;
          payButton.disabled = true;
          setState("正在创建订单...");
          try {{
            await couponLoadPromise;
            if (couponLoadFailed) throw new Error("优惠券状态加载失败，请刷新后重试");
            const payload = await createOrder();
            if (!payload) {{
              payButton.disabled = false;
              return;
            }}
            if (payload.already_paid || (payload.order && payload.order.status === "paid")) {{
              setState("已报名，正在打开报名成功页...", "success");
              showPaid(payload.order || {{}});
              return;
            }}
            activeOrderNo = payload.order.out_trade_no;
            setState("等待微信支付确认...");
            const payResult = await invokeBridge(payload.pay_params);
            const message = String(payResult.err_msg || "");
            if (message.indexOf(":ok") === -1) {{
              throw new Error(message.indexOf(":cancel") !== -1 ? "支付已取消" : "支付未完成");
            }}
            setState("正在确认支付结果...");
            const order = await waitForPaid(activeOrderNo);
            if (order.status !== "paid") {{
              throw new Error("支付结果确认中，请稍后刷新查看");
            }}
            setState("支付成功", "success");
            showPaid(order);
            if (order.success_url && !(order.lead_qr && order.lead_qr.qr_url)) {{
              window.location.href = order.success_url;
            }}
          }} catch (err) {{
            setState(err.message || "支付失败，请稍后重试", "error");
            payButton.disabled = false;
          }}
        }});
      }}

      if (mobileInput) {{
        mobileInput.addEventListener("input", function () {{
          if (mobileError) mobileError.textContent = "";
        }});
      }}
      if (showLeadQrButton) {{
        showLeadQrButton.addEventListener("click", function () {{
          showLeadQr(paidOrder || {{}});
        }});
      }}
    }})();
  </script>"""


def _page_styles() -> str:
    return """<style>
    :root {
      --ink: #17202a;
      --muted: #5f6b78;
      --line: #dbe5ee;
      --paper: #f7fbff;
      --panel: #fff;
      --gold: #ffc857;
      --gold-deep: #8a6200;
      --teal-soft: #e9f7f2;
      --teal-ink: #215a49;
      --shadow: 0 18px 46px rgba(24, 45, 68, .11);
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; min-height: 100%; }
    body.product-body {
      background:
        linear-gradient(180deg, #f4fbff 0%, #fff 44%, #f7fbff 100%);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      letter-spacing: 0;
    }
    .product-page {
      width: min(100%, 750px);
      min-height: 100vh;
      margin: 0 auto;
      padding-bottom: 104px;
      background: rgba(255, 255, 255, .86);
    }
    .detail-media { background: #fff; }
    .slice-img {
      display: block;
      width: 100%;
      min-height: 80px;
      background: #fff;
      object-fit: cover;
    }
    .hero-panel {
      margin: 18px 14px 12px;
      padding: 22px 18px 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: linear-gradient(180deg, #fff 0%, var(--paper) 100%);
      box-shadow: var(--shadow);
    }
    .eyebrow {
      color: var(--gold-deep);
      font-size: 13px;
      font-weight: 800;
      margin-bottom: 10px;
    }
    h1 {
      margin: 0;
      font-size: 44px;
      line-height: 1.08;
      font-weight: 950;
      letter-spacing: 0;
    }
    .summary {
      margin: 14px 0 0;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.65;
    }
    .product-specs {
      display: grid;
      gap: 10px;
      margin: 18px 0 0;
    }
    .product-specs div {
      display: grid;
      grid-template-columns: 88px minmax(0, 1fr);
      gap: 12px;
      align-items: start;
      padding: 12px 0;
      border-top: 1px solid rgba(234, 223, 206, .72);
    }
    .product-specs dt {
      color: var(--muted);
      font-size: 14px;
      font-weight: 800;
    }
    .product-specs dd {
      margin: 0;
      min-width: 0;
      overflow-wrap: anywhere;
      font-size: 17px;
      font-weight: 850;
    }
    .detail-section {
      display: grid;
      gap: 12px;
      padding: 0 14px 16px;
    }
    .detail-card {
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 10px 30px rgba(20, 16, 10, .06);
    }
    .detail-card h2 {
      margin: 0 0 8px;
      font-size: 18px;
    }
    .detail-card p {
      margin: 0;
      color: var(--muted);
      line-height: 1.7;
      font-size: 15px;
      white-space: pre-wrap;
    }
    .safety-note {
      margin: 0 14px 18px;
      padding: 12px 14px;
      border-radius: 8px;
      background: var(--teal-soft);
      color: var(--teal-ink);
      font-size: 13px;
      line-height: 1.55;
    }
    .safety-note strong, .safety-note span { display: block; }
    .sticky-buy {
      position: fixed;
      left: 50%;
      bottom: 0;
      z-index: 20;
      transform: translateX(-50%);
      width: min(100%, 750px);
      padding: 10px 14px calc(12px + env(safe-area-inset-bottom));
      border-top: 1px solid rgba(236, 217, 184, .86);
      background: rgba(255, 253, 248, .96);
      backdrop-filter: blur(12px);
      box-shadow: 0 -12px 34px rgba(26, 32, 48, .12);
      display: grid;
      grid-template-columns: minmax(0, 1fr) 126px;
      gap: 12px;
      align-items: center;
    }
    .sticky-title {
      color: #44515d;
      font-size: 12px;
      font-weight: 900;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .sticky-price {
      margin-top: 3px;
      color: #9a6a00;
      font-size: 24px;
      font-weight: 950;
      line-height: 1;
    }
    .sticky-price small {
      margin-right: 4px;
      font-size: 12px;
      font-weight: 900;
    }
    .cta, .disabled-pay {
      height: 52px;
      border: 0;
      border-radius: 999px;
      background: var(--gold);
      color: #2a1c07;
      box-shadow: 0 8px 18px rgba(244, 179, 69, .34);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      font-size: 15px;
      font-weight: 950;
      white-space: nowrap;
    }
    .pay-page { display: grid; place-items: start center; padding-top: 18px; }
    .pay-panel { width: calc(100% - 28px); }
    .pay-actions {
      display: grid;
      gap: 12px;
      margin-top: 18px;
    }
    .secondary-link {
      color: #6c3ca3;
      font-size: 15px;
      font-weight: 850;
    }
    .disabled-pay {
      width: 100%;
      color: rgba(42, 28, 7, .55);
      background: #eee5d5;
      box-shadow: none;
    }
    .disabled-pay:disabled { cursor: not-allowed; }
    .quiet {
      margin: 12px 0 0;
      color: var(--muted);
      font-size: 13px;
    }
    @media (max-width: 420px) {
      .product-specs div { grid-template-columns: 74px minmax(0, 1fr); }
      h1 { font-size: 36px; }
      .sticky-buy { grid-template-columns: minmax(0, 1fr) 118px; }
      .cta, .disabled-pay { height: 50px; }
    }
  </style>"""
