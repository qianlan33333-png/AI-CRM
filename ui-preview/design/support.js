/* Mini runtime for *.dc.html design-canvas exports.
 * Replaces the original design tool's support.js so the files run standalone.
 * Supports: {{ path }} interpolation, style objects, onClick handlers,
 * <sc-for list as>, <sc-if value>, state/setState re-rendering.
 */
(function () {
  'use strict';

  class DCLogic {
    constructor() {
      this.props = {};
      this.state = {};
    }
    setState(patch) {
      Object.assign(this.state, patch);
      if (this.__render) this.__render();
    }
  }

  function camelToKebab(k) {
    return k.replace(/[A-Z]/g, (m) => '-' + m.toLowerCase());
  }

  function styleObjToCss(obj) {
    return Object.entries(obj)
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => camelToKebab(k) + ':' + String(v))
      .join(';');
  }

  function resolveExpr(expr, scope) {
    expr = expr.trim();
    if (expr === 'true') return true;
    if (expr === 'false') return false;
    if (expr === 'null') return null;
    if (/^-?\d+(\.\d+)?$/.test(expr)) return Number(expr);
    const parts = expr.split('.');
    let val = scope;
    for (const p of parts) {
      if (val === null || val === undefined) return undefined;
      val = val[p];
    }
    return val;
  }

  const TOKEN_RE = /\{\{([^}]*)\}\}/g;

  function interpolate(str, scope) {
    return str.replace(TOKEN_RE, (m, expr) => {
      const v = resolveExpr(expr, scope);
      if (v === null || v === undefined) return '';
      if (typeof v === 'object') return styleObjToCss(v);
      return String(v);
    });
  }

  function walk(node, scope) {
    // Handle sc-for / sc-if (pre-transformed to <template data-sc-*>)
    if (node.nodeType === 1 && node.tagName === 'TEMPLATE') {
      const forList = node.getAttribute('data-sc-for');
      const ifVal = node.getAttribute('data-sc-if');
      if (forList !== null) {
        const as = node.getAttribute('data-as') || 'item';
        const list = resolveExpr(forList, scope) || [];
        const frag = document.createDocumentFragment();
        for (const item of list) {
          const childScope = Object.create(scope);
          childScope[as] = item;
          const clone = node.content.cloneNode(true);
          walkChildren(clone, childScope);
          frag.appendChild(clone);
        }
        node.replaceWith(frag);
        return;
      }
      if (ifVal !== null) {
        const v = resolveExpr(ifVal, scope);
        if (!v) {
          node.remove();
          return;
        }
        const clone = node.content.cloneNode(true);
        walkChildren(clone, scope);
        node.replaceWith(clone);
        return;
      }
    }

    if (node.nodeType === 3) {
      if (node.nodeValue.includes('{{')) {
        node.nodeValue = interpolate(node.nodeValue, scope);
      }
      return;
    }

    if (node.nodeType === 1) {
      // attributes
      for (const attr of Array.from(node.attributes)) {
        if (!attr.value.includes('{{')) {
          if (attr.name.startsWith('hint-')) node.removeAttribute(attr.name);
          continue;
        }
        const whole = attr.value.match(/^\{\{([^}]*)\}\}$/);
        if (/^on[a-z]+$/i.test(attr.name)) {
          const fn = whole ? resolveExpr(whole[1], scope) : null;
          node.removeAttribute(attr.name);
          if (typeof fn === 'function') {
            node.__dcBound = true;
            node.addEventListener(attr.name.slice(2).toLowerCase(), (ev) => fn(ev));
          }
        } else if (whole) {
          const v = resolveExpr(whole[1], scope);
          if (v !== null && typeof v === 'object') {
            node.setAttribute(attr.name, styleObjToCss(v));
          } else {
            node.setAttribute(attr.name, v === null || v === undefined ? '' : String(v));
          }
        } else {
          node.setAttribute(attr.name, interpolate(attr.value, scope));
        }
      }
      walkChildren(node, scope);
    }
  }

  function walkChildren(node, scope) {
    for (const child of Array.from(node.childNodes)) {
      walk(child, scope);
    }
  }

  /* ================= 全局交互反馈层 =================
   * 对所有未被 dc 脚本接管的按钮，按文案模式补齐标准反馈：
   * 保存/提交/发送/创建 → 按钮 busy + 成功 toast；删除/停用/拒绝 → 确认浮窗；
   * 复制 → toast；导出/下载 → toast；上传 → 进度浮窗；刷新/启用 → toast。
   */
  const FB = { installed: false };

  function fbEnsureUI() {
    if (document.getElementById('fb-toast')) return;
    const css = document.createElement('style');
    css.textContent = `
      #fb-toast{position:fixed;right:22px;bottom:22px;z-index:9999;background:#1F2329;color:#fff;padding:10px 16px;border-radius:8px;font-size:13px;box-shadow:0 12px 28px rgba(15,23,42,.25);display:none;font-family:inherit}
      #fb-toast.err{background:#D83931}
      #fb-mask{position:fixed;inset:0;background:rgba(15,23,42,.34);z-index:9990;display:none;align-items:center;justify-content:center;padding:24px}
      #fb-panel{width:min(420px,100%);background:#fff;border-radius:12px;box-shadow:0 24px 64px rgba(15,23,42,.22);overflow:hidden;font-family:inherit}
      #fb-head{padding:18px 18px 0;font-size:15px;font-weight:600;color:#1F2329}
      #fb-body{padding:10px 18px 18px;font-size:13px;color:#646A73;line-height:1.7}
      #fb-foot{display:flex;justify-content:flex-end;gap:10px;padding:0 18px 18px}
      .fb-btn{height:32px;padding:0 14px;border-radius:6px;border:1px solid #DEE0E3;background:#fff;color:#1F2329;font-size:13px;cursor:pointer}
      .fb-btn.primary{background:#3370ff;border-color:#3370ff;color:#fff}
      .fb-btn.danger{border-color:#F2B8B5;color:#D83931}
      #fb-prog-mask{position:fixed;inset:0;background:rgba(15,23,42,.34);z-index:9995;display:none;align-items:center;justify-content:center}
      #fb-prog{width:min(360px,90%);background:#fff;border-radius:12px;padding:22px;font-family:inherit}
      #fb-prog-track{height:6px;border-radius:99px;background:#EFF0F1;overflow:hidden;margin-top:14px}
      #fb-prog-bar{height:100%;width:0;background:#3370ff;border-radius:99px;transition:width .18s linear}
      .fb-busy{opacity:.65;pointer-events:none}
    `;
    document.head.appendChild(css);
    const mk = (html) => { const d = document.createElement('div'); d.innerHTML = html; return d.firstElementChild; };
    document.body.appendChild(mk('<div id="fb-toast"></div>'));
    document.body.appendChild(mk(`<div id="fb-mask"><div id="fb-panel">
      <div id="fb-head"></div><div id="fb-body"></div>
      <div id="fb-foot"><button class="fb-btn" id="fb-cancel">取消</button><button class="fb-btn primary" id="fb-ok">确认</button></div>
    </div></div>`));
    document.body.appendChild(mk(`<div id="fb-prog-mask"><div id="fb-prog">
      <div style="font-size:14px;font-weight:600;color:#1F2329" id="fb-prog-title">正在上传</div>
      <div id="fb-prog-track"><div id="fb-prog-bar"></div></div>
      <div style="font-size:12px;color:#8F959E;margin-top:8px" id="fb-prog-pct">0%</div>
    </div></div>`));
    document.getElementById('fb-mask').addEventListener('click', (e) => { if (e.target.id === 'fb-mask') fbHide(); });
    document.getElementById('fb-cancel').addEventListener('click', fbHide);
  }

  let fbTimer = null;
  function fbToast(msg, err) {
    fbEnsureUI();
    const t = document.getElementById('fb-toast');
    t.textContent = msg;
    t.className = err ? 'err' : '';
    t.style.display = 'block';
    clearTimeout(fbTimer);
    fbTimer = setTimeout(() => { t.style.display = 'none'; }, 2400);
  }

  let fbOnOk = null;
  function fbHide() { document.getElementById('fb-mask').style.display = 'none'; fbOnOk = null; }
  function fbConfirm(title, body, okLabel, danger, onOk) {
    fbEnsureUI();
    document.getElementById('fb-head').textContent = title;
    document.getElementById('fb-body').textContent = body;
    const ok = document.getElementById('fb-ok');
    ok.textContent = okLabel || '确认';
    ok.className = 'fb-btn ' + (danger ? 'danger' : 'primary');
    fbOnOk = onOk;
    ok.onclick = () => { fbHide(); onOk && onOk(); };
    document.getElementById('fb-mask').style.display = 'flex';
  }

  function fbBusy(btn, ms, done) {
    if (btn.__fbBusy) return;
    btn.__fbBusy = true;
    const old = btn.textContent;
    btn.classList.add('fb-busy');
    btn.textContent = '⏳ ' + old;
    setTimeout(() => {
      btn.classList.remove('fb-busy');
      btn.textContent = old;
      btn.__fbBusy = false;
      done && done();
    }, ms);
  }

  function fbUpload(btn) {
    fbEnsureUI();
    const mask = document.getElementById('fb-prog-mask');
    const bar = document.getElementById('fb-prog-bar');
    const pct = document.getElementById('fb-prog-pct');
    document.getElementById('fb-prog-title').textContent = '正在上传 · ' + (btn.textContent.trim() || '文件');
    mask.style.display = 'flex';
    let p = 0;
    const tick = setInterval(() => {
      p = Math.min(100, p + 9 + Math.random() * 12);
      bar.style.width = p + '%';
      pct.textContent = Math.floor(p) + '%';
      if (p >= 100) {
        clearInterval(tick);
        setTimeout(() => { mask.style.display = 'none'; bar.style.width = '0'; fbToast('上传完成'); }, 320);
      }
    }, 150);
  }

  function fbDelegate(e) {
    if (e.target.closest('#fb-mask') || e.target.closest('#fb-prog-mask')) return;
    const btn = e.target.closest('button');
    if (!btn || btn.__dcBound || btn.disabled) return;
    const t = btn.textContent.trim();
    if (!t || t.length > 14) return;
    if (/删除|下架/.test(t)) return fbConfirm('确认' + (/下架/.test(t) ? '下架' : '删除'), '该操作执行后不可撤销，确认继续？', '确认' + (/下架/.test(t) ? '下架' : '删除'), true, () => fbBusy(btn, 400, () => fbToast(/下架/.test(t) ? '已下架' : '已删除')));
    if (/停用|禁用/.test(t)) return fbConfirm('确认停用', '停用后相关功能将立即失效，可随时重新启用。', '确认停用', true, () => fbBusy(btn, 400, () => fbToast('已停用')));
    if (/拒绝|驳回/.test(t)) return fbConfirm('确认拒绝', '拒绝后该条数据不会进入后续流程。', '确认拒绝', true, () => fbBusy(btn, 400, () => fbToast('已拒绝')));
    if (/上传|选择文件|更换图片|更换文件/.test(t)) return fbUpload(btn);
    if (/复制/.test(t)) return fbToast('已复制到剪贴板');
    if (/导出|下载/.test(t)) return fbBusy(btn, 500, () => fbToast('任务已创建，完成后将自动下载'));
    if (/保存/.test(t)) return fbBusy(btn, 700, () => fbToast('已保存'));
    if (/提交/.test(t)) return fbBusy(btn, 700, () => fbToast('已提交'));
    if (/发布|上线/.test(t)) return fbBusy(btn, 700, () => fbToast('已发布'));
    if (/发送|群发|推送/.test(t)) return fbBusy(btn, 700, () => fbToast('已发送'));
    if (/创建|新建/.test(t)) return fbBusy(btn, 600, () => fbToast('已创建'));
    if (/刷新|重试|重新加载/.test(t)) return fbBusy(btn, 500, () => fbToast('已刷新'));
    if (/^启用$/.test(t)) return fbBusy(btn, 300, () => fbToast('已启用'));
    if (/生成/.test(t)) return fbBusy(btn, 900, () => fbToast('已生成'));
  }

  function boot() {
    const scriptEl = document.querySelector('script[data-dc-script]');
    const host = document.querySelector('x-dc');
    if (!scriptEl || !host) return;

    // props from data-props defaults
    let props = {};
    const raw = scriptEl.getAttribute('data-props');
    if (raw) {
      try {
        const spec = JSON.parse(raw);
        for (const [k, v] of Object.entries(spec)) {
          if (k.startsWith('$')) continue;
          props[k] = v && typeof v === 'object' && 'default' in v ? v.default : v;
        }
      } catch (e) { /* ignore */ }
    }

    // hoist <helmet> styles into <head>
    const helmet = host.querySelector('helmet');
    if (helmet) {
      for (const el of Array.from(helmet.children)) document.head.appendChild(el);
      helmet.remove();
    }

    // extract template BEFORE touching DOM; make sc-* table-safe via <template>
    let html = host.innerHTML;
    html = html.replace(scriptEl.outerHTML, '');
    html = html
      .replace(/<sc-for\s+([^>]*?)list="([^"]*)"([^>]*?)as="([^"]*)"([^>]*)>/g,
        (m, a, list, b, as, c) => `<template data-sc-for="${list}" data-as="${as}">`)
      .replace(/<\/sc-for>/g, '</template>')
      .replace(/<sc-if\s+([^>]*?)value="([^"]*)"([^>]*)>/g,
        (m, a, val, b) => `<template data-sc-if="${val}">`)
      .replace(/<\/sc-if>/g, '</template>');

    // note: attribute order may vary (as before list) — second pass for safety
    if (html.includes('<sc-for') || html.includes('<sc-if')) {
      html = html.replace(/<sc-for([^>]*)>/g, (m, attrs) => {
        const list = (attrs.match(/list="([^"]*)"/) || [])[1] || '';
        const as = (attrs.match(/as="([^"]*)"/) || [])[1] || 'item';
        return `<template data-sc-for="${list}" data-as="${as}">`;
      });
      html = html.replace(/<sc-if([^>]*)>/g, (m, attrs) => {
        const val = (attrs.match(/value="([^"]*)"/) || [])[1] || '';
        return `<template data-sc-if="${val}">`;
      });
    }

    const tpl = document.createElement('template');
    tpl.innerHTML = html;

    // evaluate component class
    const code = scriptEl.textContent;
    const factory = new Function('DCLogic', code + '\nreturn Component;');
    const Component = factory(DCLogic);
    const comp = new Component();
    comp.props = props;

    const container = document.createElement('div');
    container.style.display = 'contents';
    host.replaceWith(container);

    function render() {
      const vals = comp.renderVals ? comp.renderVals() : {};
      const scope = Object.create(null);
      Object.assign(scope, vals);
      const frag = tpl.content.cloneNode(true);
      walkChildren(frag, scope);
      container.replaceChildren(frag);
    }
    comp.__render = render;
    render();

    if (!FB.installed) {
      FB.installed = true;
      fbEnsureUI();
      document.addEventListener('click', fbDelegate, true);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
