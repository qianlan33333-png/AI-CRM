from __future__ import annotations

from html import escape
from typing import Mapping

from fastapi.responses import HTMLResponse


def wechat_full_service_required_response(
    *,
    status_code: int = 400,
    headers: Mapping[str, str] | None = None,
) -> HTMLResponse:
    response_headers = dict(headers or {})
    response_headers.setdefault("Cache-Control", "no-store")
    response_headers.setdefault("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
    response_headers.setdefault("Referrer-Policy", "no-referrer")
    response_headers.setdefault("X-Content-Type-Options", "nosniff")
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
  <title>请使用完整服务</title>
  <style>
    * { box-sizing: border-box; }
    html, body { margin: 0; min-height: 100%; background: #fff; }
    body { color: #17221b; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
    main { min-height: 100vh; min-height: 100dvh; display: flex; flex-direction: column; justify-content: center; padding: max(40px, env(safe-area-inset-top)) 28px max(48px, env(safe-area-inset-bottom)); text-align: center; }
    .icon { width: 76px; height: 76px; display: grid; place-items: center; margin: 0 auto 28px; border-radius: 50%; background: #e9f9ef; color: #07a944; font-size: 42px; font-weight: 900; line-height: 1; }
    h1 { max-width: 680px; margin: 0 auto; font-size: clamp(32px, 8.5vw, 44px); font-weight: 900; line-height: 1.28; letter-spacing: -.02em; }
    .reason { max-width: 620px; margin: 24px auto 0; color: #59645d; font-size: clamp(18px, 4.8vw, 22px); line-height: 1.65; }
    .instruction { max-width: 620px; margin: 32px auto 0; padding: 22px 20px; border: 2px solid #8bd8a7; border-radius: 18px; background: #effbf3; color: #123c22; font-size: clamp(22px, 5.8vw, 28px); font-weight: 850; line-height: 1.5; }
    .service-name { color: #078b38; font-weight: 950; white-space: nowrap; }
    .arrow { margin-top: 18px; color: #07a944; font-size: 42px; font-weight: 900; line-height: 1; }
  </style>
</head>
<body>
  <main role="alert" aria-labelledby="full-service-title" data-route-owner="ai_crm_next" data-identity-status="full_service_required">
    <div class="icon" aria-hidden="true">!</div>
    <h1 id="full-service-title">请点击下方的<br>“使用完整服务”<br>才能正常进行服务</h1>
    <p class="reason">当前处于微信的“部分服务”模式，登录授权信息无法保存，因此暂时不能继续支付。</p>
    <div class="instruction">请点击页面下方微信提供的<br><span class="service-name">“使用完整服务”</span></div>
    <div class="arrow" aria-hidden="true">↓</div>
  </main>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=status_code, headers=response_headers)


def wechat_identity_failure_response(
    *,
    title: str = "微信身份验证未完成",
    message: str = "当前微信账号未能获取稳定身份，请确认已同意授权后重试。",
    retry_url: str = "",
    return_url: str = "",
    status_code: int = 409,
    headers: Mapping[str, str] | None = None,
) -> HTMLResponse:
    safe_title = escape(str(title or "微信身份验证未完成"))
    safe_message = escape(str(message or "当前微信账号未能获取稳定身份。"))
    safe_retry_url = escape(str(retry_url or ""), quote=True)
    safe_return_url = escape(str(return_url or ""), quote=True)
    actions: list[str] = []
    if safe_retry_url:
        actions.append(f'<a class="primary" href="{safe_retry_url}">重新授权</a>')
    if safe_return_url:
        actions.append(f'<a class="secondary" href="{safe_return_url}">返回上一页</a>')
    action_html = "".join(actions)
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>{safe_title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; background: #f5f6f8; color: #1f2329; font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }}
    main {{ width: min(100%, 440px); padding: 28px 24px; border: 1px solid #e5e7eb; border-radius: 16px; background: #fff; box-shadow: 0 12px 36px rgba(31, 35, 41, .08); text-align: center; }}
    .icon {{ width: 52px; height: 52px; margin: 0 auto 16px; display: grid; place-items: center; border-radius: 50%; background: #fff3e8; color: #d46b08; font-size: 26px; font-weight: 800; }}
    h1 {{ margin: 0; font-size: 22px; line-height: 1.35; }}
    p {{ margin: 12px 0 0; color: #646a73; }}
    .actions {{ display: grid; gap: 10px; margin-top: 24px; }}
    a {{ min-height: 44px; display: grid; place-items: center; border-radius: 10px; font-weight: 800; text-decoration: none; }}
    .primary {{ background: #07c160; color: #fff; }}
    .secondary {{ border: 1px solid #d0d3d8; color: #3b3f46; }}
    .support {{ margin-top: 16px; font-size: 12px; color: #8f959e; }}
  </style>
</head>
<body>
  <main data-route-owner="ai_crm_next" data-identity-status="unionid_missing">
    <div class="icon">!</div>
    <h1>{safe_title}</h1>
    <p>{safe_message}</p>
    <div class="actions">{action_html}</div>
    <div class="support">若多次授权仍失败，请联系工作人员处理微信身份绑定。</div>
  </main>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=status_code, headers=dict(headers or {}))


__all__ = ["wechat_full_service_required_response", "wechat_identity_failure_response"]
