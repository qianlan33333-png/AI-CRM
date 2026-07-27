# Provider Payment Notify / Return Route Inventory

Legacy Exit group 29 closes the public provider payment notify/return wildcard rollback. Exact provider callback/return routes are owned by `aicrm_next.extensions.commerce.commerce.api`; unknown public provider child paths return controlled Next 410 responses. Admin payment and H5 payment wildcards stay out of scope.

## Provider Callback <-> API <-> Backend <-> Payment Adapter Matrix

| Provider | Caller | Action | Route | Method | Handler | Command | Adapter | External side effects | Closeout status | Smoke |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| WeChat Pay | Provider callback / smoke client | Fake notify callback | `/api/wechat-pay/notify` | POST | `aicrm_next.extensions.commerce.commerce.api.wechat_notify` | `NotifyPaymentCommand("wechat")` | `PaymentNotifyGateway` in fake or real_blocked mode | Local/fake order status update only; `provider_signature_verified=false`, `real_payment_notify_executed=false`, `real_external_call_executed=false`; no external webhook | `deletion_locked`, `legacy_fallback_allowed=false`, production_compat wildcard removed | POST notify returns `fake_signature_not_verified` and `route_owner=ai_crm_next` |
| WeChat Pay | Browser / preflight | Notify diagnostics | `/api/wechat-pay/notify` | OPTIONS | `wechat_notify_options` | n/a | fake/real_blocked contract | none | locked | OPTIONS returns Next diagnostics |
| Alipay | Provider callback / smoke client | Fake notify callback | `/api/alipay/notify` | POST | `aicrm_next.extensions.commerce.commerce.api.alipay_notify` | `NotifyPaymentCommand("alipay")` | `PaymentNotifyGateway` in fake or real_blocked mode | Local/fake order status update only; `provider_signature_verified=false`, `real_payment_notify_executed=false`, `real_external_call_executed=false`; no external webhook | `deletion_locked`, `legacy_fallback_allowed=false`, production_compat wildcard removed | POST notify returns `fake_signature_not_verified` and `route_owner=ai_crm_next` |
| Alipay | Browser / preflight | Notify diagnostics | `/api/alipay/notify` | OPTIONS | `alipay_notify_options` | n/a | fake/real_blocked contract | none | locked | OPTIONS returns Next diagnostics |
| Alipay | Browser return | Fake payment return preview | `/api/alipay/return` | GET | `alipay_return` | `PaymentReturnCommand` | `PaymentReturnGateway` in fake or real_blocked mode | No provider call, no signature verification, no order mutation | `deletion_locked`, `legacy_fallback_allowed=false`, production_compat wildcard removed | GET return responds `fake_return_received` or `fake_return_no_order` |
| Alipay | Browser / preflight | Return diagnostics | `/api/alipay/return` | OPTIONS | `alipay_return_options` | n/a | fake/real_blocked contract | none | locked | OPTIONS returns Next diagnostics |
| WeChat Pay | Unknown public provider caller | Unknown provider child | `/api/wechat-pay/{unknown_path}` | GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS | `wechat_pay_unknown` | n/a | none | none | controlled 410, no fallback | `/api/wechat-pay/unknown-child` returns `provider_payment_path_removed` |
| Alipay | Unknown public provider caller | Unknown provider child | `/api/alipay/{unknown_path}` | GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS | `alipay_unknown` | n/a | none | none | controlled 410, no fallback | `/api/alipay/unknown-child` returns `provider_payment_path_removed` |
| Admin payment | Admin payment wildcard | Out of scope | `/api/admin/wechat-pay/*`, `/api/admin/alipay/*` | mixed | production_compat retained | out of scope | fake/real_blocked | not changed in this group | retained out of scope | `/api/admin/wechat-pay/smoke` still routes to production_compat |
| H5 payment | H5 payment wildcard | Out of scope | `/api/h5/wechat-pay/*`, `/api/h5/alipay/*` | mixed | production_compat retained | out of scope | fake/real_blocked | not changed in this group | retained out of scope | `/api/h5/wechat-pay/smoke` still routes to production_compat |
| Route lifecycle | Rollback deletion | Public provider wildcard removed | `/api/wechat-pay/{path:path}`, `/api/alipay/{path:path}` | all | production_compat removed | n/a | n/a | no compatibility facade | deleted and locked | source grep has no provider public wildcard in `aicrm_next/production_compat` |

## Closeout Notes

- Notify does not perform real signature verification; every response keeps `provider_signature_verified=false`.
- Notify does not call WeChat Pay, Alipay, or any third-party provider.
- Notify may update local/fake order status and marks `source_status=fake_signature_not_verified` plus `payment_notify_executed=local_only`.
- Return does not call Alipay and marks `payment_return_executed=fake`.
- Unknown public provider paths are controlled Next responses and do not call `forward_to_legacy_flask`.
- Admin payment, H5 payment, real provider clients, real signature verification, and real callbacks remain out of scope for this group.
