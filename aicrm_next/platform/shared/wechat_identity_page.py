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
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
  <meta name="format-detection" content="telephone=no">
  <title>还差一步</title>
  <style>
    :root { --paper: #fff; --ink: #0f1114; --ink-2: #71757c; --hint: #b4b8bf; --hair: #eceef1; --accent: #07c160; }
    * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    html, body { min-height: 100%; }
    body { min-height: 100vh; min-height: 100dvh; display: flex; flex-direction: column; align-items: center; padding: calc(env(safe-area-inset-top) + 20px) 28px calc(env(safe-area-inset-bottom) + 18px); background: var(--paper); color: var(--ink); font: 400 15px/1.7 -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; -webkit-font-smoothing: antialiased; text-align: center; }
    .brand { font-size: 13px; letter-spacing: .08em; color: var(--hint); }
    .stage { width: 100%; flex: 1; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; }
    h1 { font-size: 27px; font-weight: 700; letter-spacing: -.01em; line-height: 1.3; }
    .sub { max-width: 15em; margin-top: 10px; color: var(--ink-2); font-size: 15px; }
    .rail { width: 1px; height: 60px; margin: 26px 0 14px; background: linear-gradient(to bottom, var(--hair), var(--accent)); }
    .cue { font-size: 15px; font-weight: 500; }
    .cue b { color: var(--accent); font-weight: 600; }
    .chev { margin-top: 10px; color: var(--accent); animation: dip 1.6s ease-in-out infinite; }
    .fallback { margin-top: 22px; color: var(--hint); font-size: 12px; opacity: 0; animation: reveal-fallback 0s linear 8s forwards; }
    @keyframes dip { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(6px); } }
    @keyframes reveal-fallback { to { opacity: 1; } }
    @media (prefers-reduced-motion: reduce) { .chev { animation: none; } }
  </style>
</head>
<body>
  <div class="brand">新流商业</div>
  <main class="stage" role="alert" aria-labelledby="full-service-title" data-route-owner="ai_crm_next" data-identity-status="full_service_required">
    <h1 id="full-service-title">还差一步</h1>
    <p class="sub">微信当前是「部分服务」模式，登录状态无法保存</p>
    <div class="rail" aria-hidden="true"></div>
    <p class="cue">点击屏幕底部的 <b>使用完整服务</b></p>
    <svg class="chev" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
      <path d="M6 9l6 6 6-6"></path>
    </svg>
    <p class="fallback">没看到底部提示？点右上角 ··· 刷新后重试</p>
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
