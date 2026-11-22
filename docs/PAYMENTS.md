# 微信支付（JSAPI / 小程序，APIv3）

服务端下单、验签和解密。小程序只负责拿签名参数调 `wx.requestPayment`。`wx.requestPayment` 的 success **不是**开通依据。会员只在微信支付通知验签通过，或服务端主动向微信查单确认后开通。

正式商户联调仍待验。下面没有商户号、密钥、回调域名或 IP。

## 环境变量

全部只在服务器环境里设置。缺任何一项时，下单和查单补偿返回 **503** `支付未配置`，进程不退出。

| 变量 | 用途 |
|---|---|
| `NADOU_WX_APPID` | 小程序 AppID，登录和 JSAPI 共用 |
| `NADOU_WECHAT_MCH_ID` | 商户号 |
| `NADOU_WECHAT_API_V3_KEY` | APIv3 密钥（32 字节） |
| `NADOU_WECHAT_MCH_PRIVATE_KEY_PATH` | 商户 API 私钥 PEM 文件路径 |
| `NADOU_WECHAT_MCH_CERT_SERIAL` | 商户 API 证书序列号 |
| `NADOU_WECHAT_PAY_PUBLIC_KEY_PATH` | 微信支付公钥，或平台证书 PEM 文件路径 |
| `NADOU_WECHAT_PAY_PUBLIC_KEY_ID` | 上面那把公钥的 ID（回调头 `Wechatpay-Serial` 必须一致） |
| `NADOU_WECHAT_NOTIFY_URL` | 公网 `https://` 回调地址，路径为 `/api/v1/pay/wechat/notify` |
| `NADOU_WECHAT_PAY_TEST_TIER` | `1` 才允许考虑测试档。默认关闭 |
| `NADOU_WECHAT_PAY_TEST_USERS` | 允许看到并购买测试档的用户 id，逗号分隔。默认空。`membership_test` 和以后的 `membership_dev` 共用这份名单 |

私钥和证书放在仓库外。不要把文件提交进 git。不要把白名单里的用户 id 写进仓库。

测试档要同时满足两件事：`NADOU_WECHAT_PAY_TEST_TIER` 为打开，且当前用户 id 在 `NADOU_WECHAT_PAY_TEST_USERS` 里。名单为空、或用户不在名单里时，档位列表不返回测试档，下单传 `membership_test` 返回 **400** `档位无效`。开关关闭时也一样，即使 id 写在名单里。

`product` 属于 `TEST_PRODUCTS`（`membership_test` 和体验版切换写入的 `membership_dev`）的订单不进入工作台的付费转化和营收统计。正式档照常计入。订单列表里仍能看到这些订单。体验版切换接口拆除之后，`membership_dev` 仍留在这个集合和 `ck_orders_product` 里：拿掉之后，这些金额为 0 的历史订单会重新算进付费转化；收紧约束还要再迁移，库里还有这种行时会失败。

## 上线检查清单

正式版提审前做完这三件事：

1. 把 `NADOU_WECHAT_PAY_TEST_TIER` 设为 `0`。
2. 清空 `NADOU_WECHAT_PAY_TEST_USERS`。
3. 用已登录账号请求 `GET /api/v1/pay/tiers`，确认返回里没有 `membership_test`。

## 档位

金额由服务端决定。请求里的 `amount` 即使传了也会被忽略。

| `tier` | 会员 | 时长 | 金额 |
|---|---|---|---|
| `membership_regular` | 普通会员 `regular` | 30 天 | 29900 分（299.00 元） |
| `membership_vip` | VIP `vip` | 30 天 | 59900 分（599.00 元） |
| `membership_test` | 普通会员 `regular` | 1 天 | 1 分（0.01 元），仅开关打开且用户在白名单内 |

开通走已有的 `OrderGrantWriter`：按订单上的会员档位和天数写入一条 `source=order` 的权益。这条权益的到期时间是 `max(该档当前到期日, 付款时刻) + 天数`。该档当前到期日与 `GET /api/v1/auth/me` 的 `vip_expires_at` 或 `regular_expires_at` 相同，即该档所有未过期权益里最晚的 `expires_at`（订单、手动发放、邀请码、以及 `source` 为空的都算）。到期时刻等于现在的视为已过期，不作为起点。普通会员和 VIP 分开计：购买一档不改另一档，也不把另一档的剩余天数加到这一档上。同一订单重复通知不会再写一条，也不会再顺延。

## 小程序要调的接口

除回调外，请求都带登录接口发的 `Authorization: Bearer <token>`。响应 JSON 形如 `{"detail":"..."}`，除非另有说明。

### 1. 档位列表

`GET /api/v1/pay/tiers`

无请求体。

200：

```json
{
  "tiers": [
    {
      "id": "membership_vip",
      "title": "VIP",
      "membership": "vip",
      "duration_days": 30,
      "amount_fen": 59900,
      "amount_yuan": "599.00"
    }
  ]
}
```

`amount_fen` 是人民币分，整数。`amount_yuan` 是展示用的元字符串。开关关闭，或当前用户不在 `NADOU_WECHAT_PAY_TEST_USERS` 里时，数组里没有 `membership_test`。

| HTTP | `detail` | 何时 |
|---|---|---|
| 401 | 未登录 | 没有、无效或过期的 Bearer |
| 403 | account is disabled | 账号已封禁 |

### 2. 下单并拿 `wx.requestPayment` 参数

`POST /api/v1/pay/orders`

```json
{ "tier": "membership_vip" }
```

`tier` 取值见上表。可以多传 `amount`（整数），服务端不看它。

200：

| 字段 | 含义 |
|---|---|
| `order_number` | 商户订单号。后面查单用它 |
| `tier` | 请求的档位 id |
| `title` | 展示名 |
| `membership` | 将开通的 `regular` 或 `vip` |
| `duration_days` | 天数 |
| `amount_fen` | 实际标价，分 |
| `status` | 新建时为 `待支付` |
| `paid_at` | 未支付时为 `null` |
| `granted` | 新建时为 `false` |
| `timeStamp` | 字符串，直接给 `wx.requestPayment` |
| `nonceStr` | 直接给 `wx.requestPayment` |
| `package` | `prepay_id=...`，直接给 `wx.requestPayment` |
| `signType` | 固定 `RSA` |
| `paySign` | 直接给 `wx.requestPayment` |

`wx.requestPayment` 只传这五个字段：`timeStamp`、`nonceStr`、`package`、`signType`、`paySign`。不要传 `appId`。保存 `order_number`。

| HTTP | `detail` | 何时 |
|---|---|---|
| 401 | 未登录 | 没有、无效或过期的 Bearer |
| 403 | account is disabled | 账号已封禁 |
| 400 | 档位无效 | `tier` 为空、未知，或测试档未对当前用户开放（开关关闭，或不在白名单） |
| 422 | FastAPI 校验错误 | 正文不是 JSON，或 `tier` 不是字符串 |
| 503 | 支付未配置 | 上面任一支付环境变量缺失、密钥文件读不到，或 APIv3 密钥不是 32 字节 |
| 409 | 缺少微信 openid | 用户没有 openid（正常微信登录不会出现） |
| 502 | 微信支付下单失败 | 微信下单接口失败。本地订单不会留下 |

### 3. 支付之后怎么查

客户端 success、fail、取消，都按下面做。不要根据 `wx.requestPayment` 的结果改会员。

1. `POST /api/v1/pay/orders/{order_number}/sync`  
   无请求体。服务端向微信查这笔商户订单号。微信返回 `SUCCESS` 且商户号、AppID、订单号、金额、openid 都与本地订单一致时，才开通会员。请求体里写「已支付」没有作用。
2. 若这时 `status` 仍是 `待支付`，可以过几秒再 `GET /api/v1/pay/orders/{order_number}`。这是只读本地状态，**不会**开通。微信通知可能比查单先到。
3. `granted` 为 `true`（`status` 为 `已发放`）之后，再 `GET /api/v1/auth/me` 刷新会员。

查单和同步的 200 字段与下单相同，但没有 `timeStamp`、`nonceStr`、`package`、`signType`、`paySign`。

`status`：`待支付`、`已支付`、`已发放`、`发放异常`、`已退款`。`granted` 只在 `已发放` 时为 `true`。`发放异常` 表示钱已确认但权益写入失败，需要工作台补发，客户端不要再当成未支付去开一笔新的同等订单之前先把这个状态展示给用户。

| HTTP | `detail` | 何时 |
|---|---|---|
| 401 | 未登录 | 没有、无效或过期的 Bearer |
| 403 | account is disabled | 账号已封禁 |
| 404 | 订单不存在 | 订单号不对，或不是当前用户的订单 |
| 503 | 支付未配置 | 仅 `sync`：支付环境不完整 |
| 502 | 微信支付查询失败 | 仅 `sync`：微信查单失败 |
| 400 | 金额不符 | 仅 `sync`：微信称支付成功，但金额与本地订单不一致。不开通 |
| 400 | 支付校验失败 | 仅 `sync`：商户号、AppID、openid 或微信交易号与订单不一致。不开通 |

`GET` 订单不返回 503/502/金额不符，因为它不访问微信。

### 4. 会员状态

`GET /api/v1/auth/me`

同样带 Bearer。这是已有的登录接口，未登录时 **401** 的 `detail` 是 `authentication required`，不是 `未登录`。小程序把任意 401 当成需要重新登录即可。

200：

| 字段 | 含义 |
|---|---|
| `id` | 用户 id |
| `tier` | `vip`、`regular` 或 `free`。有未过期 VIP 时为 `vip` |
| `vip_expires_at` | VIP 到期时间，ISO 8601，没有则为 `null` |
| `regular_expires_at` | 普通会员到期时间，没有则为 `null` |

支付测试档开通的是 1 天 `regular`。只有这笔权益时，`tier` 为 `regular`。

## 回调（不是小程序接口）

`POST /api/v1/pay/wechat/notify`

微信服务器调用。验签用微信支付公钥（或平台证书），解密用 APIv3 密钥。验签通过、商户号、AppID、订单号、金额与本地一致且 `trade_state` 为 `SUCCESS` 才开通。重复通知返回成功且不会再次开通。

验签或业务校验失败时返回 4xx/5xx 和 `{"code":"FAIL","message":"失败"}`，不开通。通过时返回 **204** 空正文，包括重复通知，以及已验签但状态不是 `SUCCESS` 的通知。

日志只记订单号、状态码和拒绝原因，不记密钥、私钥或解密后的回调正文。
