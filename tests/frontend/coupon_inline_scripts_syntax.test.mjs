import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const TEMPLATE_FILES = [
  "aicrm_next/extensions/commerce/commerce/coupons/templates/admin_console/coupon_form.html",
  "aicrm_next/extensions/commerce/commerce/coupons/templates/admin_console/coupon_list.html",
  "aicrm_next/extensions/commerce/commerce/coupons/templates/admin_console/coupon_data.html",
  "aicrm_next/extensions/commerce/commerce/coupons/templates/coupon_public.html",
];

function executableScripts(html) {
  const scripts = [];
  const pattern = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
  for (const match of html.matchAll(pattern)) {
    if (/type\s*=\s*["']application\/json["']/i.test(match[1])) continue;
    scripts.push(match[2]);
  }
  return scripts;
}

function substituteJinja(source) {
  return source
    .replace(/{#[\s\S]*?#}/g, "")
    .replace(/{%[\s\S]*?%}/g, "")
    .replace(/{{[\s\S]*?}}/g, "null");
}

for (const relativePath of TEMPLATE_FILES) {
  const html = fs.readFileSync(path.join(ROOT, relativePath), "utf8");
  const scripts = executableScripts(html);
  assert.ok(scripts.length > 0, `${relativePath} must contain executable JavaScript`);
  scripts.forEach((source, index) => {
    new vm.Script(substituteJinja(source), { filename: `${relativePath}#script-${index + 1}` });
  });
}

const publicCoupon = fs.readFileSync(
  path.join(ROOT, "aicrm_next/extensions/commerce/commerce/coupons/templates/coupon_public.html"),
  "utf8",
);
assert.match(publicCoupon, /coupon_unavailable:\s*"当前优惠券暂不可领取"/);
assert.match(publicCoupon, /per-user coupon claim limit reached/);
assert.match(publicCoupon, /\^\[a-z\]\[a-z0-9\]\*_\[a-z0-9_\]\+\$/);

const paymentRenderer = fs.readFileSync(
  path.join(ROOT, "aicrm_next/extensions/commerce/public_product/service.py"),
  "utf8",
);
assert.match(paymentRenderer, /coupon_choice:\s*couponChoice\(\)/);
assert.match(paymentRenderer, /clearCompletedClientOrderRef\(\)/);
assert.match(paymentRenderer, /sessionStorage\.removeItem\(clientOrderStorageKey\)/);
assert.match(paymentRenderer, /const publicErrorMessages/);
assert.match(paymentRenderer, /identity_conflict:\s*"当前微信身份存在冲突，请联系客服处理。"/);
assert.match(paymentRenderer, /wechat_pay_order_refresh_failed/);

console.log("coupon admin and claim inline JavaScript syntax; checkout contracts OK");
