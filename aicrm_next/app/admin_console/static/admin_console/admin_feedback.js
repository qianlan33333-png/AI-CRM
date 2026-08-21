/* AI-CRM 后台全局交互反馈层
 * 全量覆盖：表单提交 busy + 防重复提交、危险操作确认浮窗、文件选择与上传提示。
 * 原则：只补充反馈，不改变任何既有业务流程；已被页面脚本接管的交互自动跳过。
 */
(function () {
  "use strict";
  if (window.__afbInstalled) return;
  window.__afbInstalled = true;

  /* ---------- 样式 ---------- */
  var css = [
    ".afb-busy{position:relative;color:transparent!important;pointer-events:none}",
    ".afb-busy::after{content:'';position:absolute;left:50%;top:50%;width:14px;height:14px;margin:-8px 0 0 -8px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;color:#fff;animation:afb-spin .7s linear infinite}",
    ".afb-busy.afb-busy-light::after{color:#344054}",
    "@keyframes afb-spin{to{transform:rotate(360deg)}}",
    "#afb-toast-wrap{position:fixed;right:22px;bottom:22px;z-index:9999;display:grid;gap:8px;justify-items:end}",
    ".afb-toast{background:#1F2329;color:#fff;padding:10px 16px;border-radius:8px;font-size:13px;box-shadow:0 12px 28px rgba(15,23,42,.25);animation:afb-in .18s ease-out;max-width:min(420px,80vw);line-height:1.5}",
    ".afb-toast.afb-err{background:#D83931}",
    "@keyframes afb-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}",
    "#afb-mask{position:fixed;inset:0;background:rgba(15,23,42,.34);z-index:9998;display:flex;align-items:center;justify-content:center;padding:24px;animation:afb-in .15s ease-out}",
    "#afb-card{width:min(440px,100%);background:#fff;border-radius:12px;box-shadow:0 24px 64px rgba(15,23,42,.22);overflow:hidden}",
    "#afb-title{padding:18px 18px 0;font-size:15px;font-weight:600;color:#1F2329}",
    "#afb-body{padding:10px 18px 18px;font-size:13px;color:#646A73;line-height:1.8}",
    "#afb-btns{display:flex;justify-content:flex-end;gap:10px;padding:0 18px 18px}",
    "#afb-btns button{height:32px;padding:0 14px;border-radius:6px;font-size:13px;cursor:pointer}",
    "#afb-cancel{border:1px solid #DEE0E3;background:#fff;color:#1F2329}",
    "#afb-ok{border:0;background:#3370FF;color:#fff}",
    "#afb-ok.afb-danger{background:#fff;border:1px solid #F2B8B5;color:#D83931}",
    "#afb-upbar{position:fixed;top:0;left:0;right:0;height:3px;z-index:9999;background:transparent}",
    "#afb-upbar>i{display:block;height:100%;width:40%;background:#3370FF;border-radius:99px;animation:afb-up 1.1s ease-in-out infinite}",
    "@keyframes afb-up{0%{margin-left:-40%}100%{margin-left:100%}}",
    "#afb-upbar em{position:fixed;top:10px;left:50%;transform:translateX(-50%);background:#1F2329;color:#fff;font-size:12px;font-style:normal;padding:5px 12px;border-radius:99px;box-shadow:0 8px 20px rgba(15,23,42,.25)}",
  ].join("");
  var style = document.createElement("style");
  style.id = "afb-style";
  style.textContent = css;
  (document.head || document.documentElement).appendChild(style);

  /* ---------- Toast ---------- */
  function toast(msg, isErr) {
    var wrap = document.getElementById("afb-toast-wrap");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.id = "afb-toast-wrap";
      document.body.appendChild(wrap);
    }
    var el = document.createElement("div");
    el.className = "afb-toast" + (isErr ? " afb-err" : "");
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(function () { el.remove(); }, 2600);
  }

  /* ---------- 确认浮窗 ---------- */
  function confirmBox(opts) {
    return new Promise(function (resolve) {
      var mask = document.createElement("div");
      mask.id = "afb-mask";
      mask.innerHTML =
        '<div id="afb-card" role="dialog" aria-modal="true">' +
        '<div id="afb-title"></div>' +
        '<div id="afb-body"></div>' +
        '<div id="afb-btns"><button type="button" id="afb-cancel">取消</button>' +
        '<button type="button" id="afb-ok"></button></div></div>';
      mask.querySelector("#afb-title").textContent = opts.title || "确认操作";
      mask.querySelector("#afb-body").textContent = opts.body || "";
      var ok = mask.querySelector("#afb-ok");
      ok.textContent = opts.okLabel || "确认";
      if (opts.danger) ok.classList.add("afb-danger");
      var done = function (v) {
        document.removeEventListener("keydown", onKey, true);
        mask.remove();
        resolve(v);
      };
      var onKey = function (e) { if (e.key === "Escape") done(false); };
      document.addEventListener("keydown", onKey, true);
      mask.addEventListener("click", function (e) { if (e.target === mask) done(false); });
      mask.querySelector("#afb-cancel").addEventListener("click", function () { done(false); });
      ok.addEventListener("click", function () { done(true); });
      document.body.appendChild(mask);
      ok.focus();
    });
  }

  /* ---------- 表单提交：busy + 防重复提交 ---------- */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.__afbSubmitting) {
      e.preventDefault();
      return;
    }
    form.__afbSubmitting = true;
    setTimeout(function () { form.__afbSubmitting = false; }, 15000);
    var btn = e.submitter || form.querySelector('[type="submit"]');
    if (btn) {
      var bg = "";
      try { bg = getComputedStyle(btn).backgroundColor || ""; } catch (_e) {}
      var m = bg.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
      var lum = m ? (0.299 * Number(m[1]) + 0.587 * Number(m[2]) + 0.114 * Number(m[3])) : 0;
      btn.classList.add("afb-busy");
      if (lum >= 140) btn.classList.add("afb-busy-light");
      setTimeout(function () { btn.classList.remove("afb-busy", "afb-busy-light"); }, 15000);
    }
    var fileInput = form.querySelector('input[type="file"]');
    if (fileInput && fileInput.files && fileInput.files.length) showUploadBar();
  });
  window.addEventListener("pageshow", function () {
    document.querySelectorAll(".afb-busy").forEach(function (b) { b.classList.remove("afb-busy", "afb-busy-light"); });
    hideUploadBar();
  });

  /* ---------- 上传指示 ---------- */
  function showUploadBar() {
    if (document.getElementById("afb-upbar")) return;
    var bar = document.createElement("div");
    bar.id = "afb-upbar";
    bar.innerHTML = "<i></i><em>文件上传中，请稍候…</em>";
    document.body.appendChild(bar);
  }
  function hideUploadBar() {
    var bar = document.getElementById("afb-upbar");
    if (bar) bar.remove();
  }
  document.addEventListener("change", function (e) {
    var input = e.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "file") return;
    if (!input.files || !input.files.length) return;
    var total = 0;
    for (var i = 0; i < input.files.length; i++) total += input.files[i].size || 0;
    var mb = total / 1048576;
    var sizeText = mb >= 1 ? mb.toFixed(1) + " MB" : Math.max(1, Math.round(total / 1024)) + " KB";
    var name = input.files.length === 1 ? input.files[0].name : input.files.length + " 个文件";
    toast("已选择：" + name + "（" + sizeText + "）");
  });

  /* ---------- 危险操作确认 ---------- */
  var DANGER_RE = /删除|下架|停用|禁用|驳回|拒绝|作废|清空/;
  var IRREVERSIBLE_RE = /删除|下架|作废|清空/;
  function hasNativeConfirm(el, form) {
    var oc = (el.getAttribute("onclick") || "") + " " + (form ? form.getAttribute("onsubmit") || "" : "");
    return oc.indexOf("confirm") !== -1;
  }
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented) return;
    var el = e.target && e.target.closest ? e.target.closest("a,button") : null;
    if (!el || el.__afbPass) return;
    if (el.hasAttribute("data-copy-text") || el.hasAttribute("data-output-review-reject")) return;
    if (el.closest("#afb-mask")) return;
    var text = (el.textContent || "").trim();
    if (!text || text.length > 12 || !DANGER_RE.test(text)) return;

    var isLink = el.tagName === "A" && el.getAttribute("href") && el.getAttribute("href").charAt(0) !== "#";
    var form = el.tagName === "BUTTON" ? el.form : null;
    var isSubmitter = !!form && (el.type === "submit" || el.type === "");
    if (!isLink && !isSubmitter) return; // type=button 一律视为页面脚本已接管
    if (hasNativeConfirm(el, form)) return;

    var action = (text.match(DANGER_RE) || ["操作"])[0];
    e.preventDefault();
    confirmBox({
      title: "确认" + action,
      body: IRREVERSIBLE_RE.test(action)
        ? "「" + text + "」执行后不可撤销，确认继续？"
        : "「" + text + "」执行后将立即生效，可随时恢复，确认继续？",
      okLabel: "确认" + action,
      danger: true,
    }).then(function (yes) {
      if (!yes) return;
      el.__afbPass = true;
      el.click(); // 再次触发：链接直接跳转 / 按钮走原生提交
    });
  });

  window.AdminFb = { toast: toast, confirm: confirmBox };
})();
