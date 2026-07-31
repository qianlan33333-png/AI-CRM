# Public Product / Pay Route Inventory

Legacy Exit Group 27 closes the public product and pay landing rollback for `/p/*`, `/pay/*`, and `/api/products/*`.

## Frontend <-> API <-> Backend Contract Matrix

| 入口 | 调用方 | 动作 | Route | Method | Handler | Backend | 外部副作用 | Closeout 状态 | Smoke |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 商品详情页 | QR/share/sidebar product_url | render product display | `/p/{product_or_slug}` | GET/HEAD | `aicrm_next.extensions.commerce.public_product.api.public_product_page` | `CommerceRepository.get_product_by_slug/get_product_by_code` + `preview_product` | none; `payment_request_executed=false`; `order_create_executed=false` | `legacy_fallback_allowed=false`; `deletion_locked`; `replacement_status=locked` | 200 or controlled 404, never 500 |
| 商品详情图片 | public product page | serve bound product slice image variant | `/api/h5/product-images/{product_code}/{image_id}/variants/{variant_key}` | GET | `aicrm_next.extensions.commerce.public_product.api.h5_public_product_image_variant` | product slice ownership check + media library variant query | none; binary cache response only | `legacy_fallback_allowed=false`; `deletion_locked`; `replacement_status=locked` | 200 with cache headers or controlled 404 |
| 支付落地页 | legacy `/pay/<product_code>` link | render H5 WeChat Pay entry | `/pay/{product_or_slug}` | GET/HEAD | `aicrm_next.extensions.commerce.public_product.api.public_pay_landing` | same product projection + H5 identity state | guarded H5 payment entry; no order is created by page render | `legacy_fallback_allowed=false`; `deletion_locked`; `replacement_status=locked` | 200 or controlled 404, page contains JSAPI checkout script |
| Product API detail | public frontend / legacy share clients | read product contract | `/api/products/{path}` | GET/HEAD | `aicrm_next.extensions.commerce.public_product.api.public_product_api` | product projection | none for detail/list | `legacy_fallback_allowed=false`; `deletion_locked`; `replacement_status=locked` | 200 known, 404 unknown |
| Product API list | diagnostics/public readers | read active product list | `/api/products/list` | GET/HEAD | `public_product_api` | `list_products` filtered to enabled display projection | none | locked | 200 contract |
| checkout-like child path | old clients or probes | block payment action | `/api/products/{path containing checkout/payment/order}` | GET/HEAD | `public_product_api` | no repository write | blocked; `payment_request_executed=false`; `order_create_executed=false` | locked | 410 controlled |
| write-like product path | old clients or probes | block write/payment action | `/api/products/{path}` | POST/PUT/PATCH/DELETE | `public_product_api_blocked_write` | none | blocked; no order create; no provider call | locked | 410 controlled |
| unknown child path | bad URL/manual probes | controlled not found | `/api/products/{unknown}` | GET/HEAD | `public_product_api` | product lookup only | none | locked | 404 controlled |
| production_compat exact rollback | legacy fallback | removed | `/p/*`, `/pay/*`, `/api/products/*` | all | removed from `router` | none | production_compat rollback removed | grep clean |
| production_compat wildcard rollback | broad fallback | removed | `/p/*`, `/pay/*`, `/api/products/*` | all | removed from `wildcard_router` | none | wildcard_router rollback removed | grep clean |
| H5 WeChat Pay | public pay page | OAuth, JSAPI order create, order status, notify | `/api/h5/wechat-pay/*` | mixed | `aicrm_next.extensions.commerce.public_product.api` + `h5_wechat_pay` | Next-owned WeChat Pay client | guarded live WeChat Pay; APIv3 responses and notifications require RSA signature verification, matching platform public-key ID/certificate serial, and a ±300-second timestamp window | locked, no production_compat rollback | smoke checks route ownership and controlled failure outside WeChat |
| payment/admin/alipay/checkout/orders/provider | out-of-scope | later groups own remaining payment APIs | `/api/admin/wechat-pay/*`, `/api/admin/alipay/*`, `/api/h5/alipay/*`, `/api/orders/*`, `/api/checkout/*`, `/api/wechat-pay/*`, `/api/alipay/*` | all | later group owners unchanged | guarded/blocked by separate groups | checkout/orders locked in group 28; public provider notify/return locked in group 29; admin/alipay remain out-of-scope | smoke retained families |

## Boundary Decisions

- `/p/{path}` is a public product/detail landing path used by share URLs and sidebar product links.
- `/pay/{path}` is treated as a public pay landing that can launch the Next-owned H5 WeChat Pay flow after identity checks.
- `/api/products/{path}` is read/display contract only; payment/action paths are blocked.
- `/api/h5/product-images/{product_code}/{image_id}/variants/{variant_key}` only serves media library variants for images already attached to enabled public product page slices; it does not expose admin media paths as public page resources.
- Known child APIs in this group: detail by slug/code, list, blocked checkout/payment/order child path, unknown path.
- Lead channel and completion redirect fields may be present in the product projection; H5 WeChat Pay may return paid-order lead QR state after a confirmed paid order.
- Next-owned H5 WeChat Pay may create JSAPI orders through `/api/h5/wechat-pay/*` after WeChat identity, payment configuration, and production database checks pass.
- The Next-owned WeChat Pay client rejects unsigned, stale/future-dated, serial-mismatched, or cryptographically invalid APIv3 responses and notifications before parsing provider payloads.
- Do not process real Alipay in this group.
- Do not change admin/alipay/checkout/orders/provider ownership in this group; checkout/orders and public provider notify/return remain separate route families.
