# Checkout / Orders Public API Route Inventory

Legacy Exit group 28 closes the public checkout and order API wildcard rollback. The exact Next routes are owned by `aicrm_next.extensions.commerce.commerce.api`; unknown checkout/order child paths are controlled Next 404/410 responses. Provider notify/return, admin payment, and H5 payment wildcards stay out of scope.

## API <-> Backend <-> Payment Adapter Contract Matrix

| Caller | Action | Route | Method | Handler | Command/Query | Adapter | External side effects | Closeout status | Smoke |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Public pay landing / API client | Start WeChat checkout | `/api/checkout/wechat` | POST | `aicrm_next.extensions.commerce.commerce.api.checkout_wechat` | `CheckoutCommand("wechat")` | `WeChatPayAdapter` in fake or real_blocked mode | Local fake order only; `payment_request_executed=false`, `real_external_call_executed=false`, `real_wechat_pay_executed=false` | `deletion_locked`, `legacy_fallback_allowed=false`, production_compat wildcard removed | POST checkout wechat returns `order_no`, `fake_payment=true`, `route_owner=ai_crm_next` |
| Public pay landing / API client | Start Alipay checkout | `/api/checkout/alipay` | POST | `aicrm_next.extensions.commerce.commerce.api.checkout_alipay` | `CheckoutCommand("alipay")` | `AlipayAdapter` in fake or real_blocked mode | Local fake order only; `payment_request_executed=false`, `real_external_call_executed=false`, `real_alipay_executed=false` | `deletion_locked`, `legacy_fallback_allowed=false`, production_compat wildcard removed | POST checkout alipay returns `order_no`, `fake_payment=true`, `route_owner=ai_crm_next` |
| Browser / preflight | Checkout diagnostics | `/api/checkout/wechat` | OPTIONS | `checkout_wechat_options` | n/a | fake/real_blocked contract | none | locked | OPTIONS returns Next diagnostics |
| Browser / preflight | Checkout diagnostics | `/api/checkout/alipay` | OPTIONS | `checkout_alipay_options` | n/a | fake/real_blocked contract | none | locked | OPTIONS returns Next diagnostics |
| Public order view / poller | Read order | `/api/orders/{order_no}` | GET | `get_order` | `GetOrderQuery` | none | none; local repository read only | `deletion_locked`, `legacy_fallback_allowed=false`, production_compat wildcard removed | GET order returns `source_status=next_order_read` |
| Public order view / poller | Read payment status | `/api/orders/{order_no}/status` | GET | `get_order` | `GetOrderQuery` | none | none; local repository read only | `deletion_locked`, `legacy_fallback_allowed=false`, production_compat wildcard removed | GET order status returns `source_status=next_order_read` |
| Browser / preflight | Order diagnostics | `/api/orders/{order_no}` | OPTIONS | `get_order_options` | n/a | none | none | locked | OPTIONS returns Next diagnostics |
| Browser / preflight | Order status diagnostics | `/api/orders/{order_no}/status` | OPTIONS | `get_order_status_options` | n/a | none | none | locked | OPTIONS returns Next diagnostics |
| Legacy or unknown caller | Unknown checkout child | `/api/checkout/{unknown_path}` | GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS | `checkout_unknown` | n/a | none | none | controlled 410, no fallback | `/api/checkout/unknown-child` returns `checkout_path_removed` |
| Legacy or unknown caller | Unknown order child | `/api/orders/{order_no}/{unknown_child_path}` | GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS | `order_unknown_child` | n/a | none | none | controlled 410, no fallback | `/api/orders/smoke/legacy-child` returns `order_child_path_removed` |
| Payment provider | WeChat Pay notify / fallback family | `/api/wechat-pay/*` | mixed | group 29 owner | group 29 | fake/real_blocked | handled by provider payment closeout | deletion_locked in group 29 | `/api/wechat-pay/notify` and unknown child route to Next |
| Payment provider | Alipay notify / return / fallback family | `/api/alipay/*` | mixed | group 29 owner | group 29 | fake/real_blocked | handled by provider payment closeout | deletion_locked in group 29 | `/api/alipay/notify`, `/api/alipay/return`, and unknown child route to Next |
| Admin payment | Admin payment wildcard | `/api/admin/wechat-pay/*`, `/api/admin/alipay/*` | mixed | out of scope | out of scope | fake/real_blocked | not changed in this group | retained out of scope | `/api/admin/wechat-pay/smoke` still routes to production_compat |
| H5 payment | H5 payment wildcard | `/api/h5/wechat-pay/*`, `/api/h5/alipay/*` | mixed | out of scope | out of scope | fake/real_blocked | not changed in this group | retained out of scope | H5 payment not changed |
| Route lifecycle | Rollback deletion | `/api/checkout/{path:path}`, `/api/orders/{path:path}` | all | production_compat removed | n/a | n/a | no compatibility facade | deleted and locked | source grep has no checkout/orders wildcard in `aicrm_next/production_compat` |

## Closeout Notes

- Checkout creates only a local/fake order in the Next commerce repository and marks `order_create_executed=local_only`.
- Checkout responses expose `fake_payment=true`, `adapter_mode=fake/real_blocked`, `fallback_used=false`, `payment_request_executed=false`, and `real_external_call_executed=false`.
- Order read/status responses expose `source_status=next_order_read`, `route_owner=ai_crm_next`, and `fallback_used=false`.
- Unknown checkout/order child paths are controlled Next responses and do not call `forward_to_legacy_flask`.
- `/api/orders/{unknown_child_path}` is represented by the concrete Next route `/api/orders/{order_no}/{unknown_child_path}` and is closed with controlled 410.
- provider notify/return was closed in group 29; admin payment, H5 payment, real WeChat Pay, real Alipay, and real callbacks remain out of scope.
