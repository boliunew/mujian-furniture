# 🪵 Mujian Furniture — Stripe 版

美国家具网店：美元标价、英文界面，付款走 Stripe Checkout（Stripe 托管的支付页，支持信用卡、Apple Pay、Google Pay）。

## 📁 文件

| 文件 | 作用 |
|---|---|
| `server.py` | Flask 后端：商品目录和价格、创建 Stripe 支付、接收 Webhook、SQLite 存订单 |
| `public/index.html` | 商城首页 + 购物车 |
| `public/success.html` | 付款成功后的订单页 |
| `public/style.css`, `public/art.js` | 样式和家具插图 |
| `.env.example` | 配置模板，复制成 `.env` 填密钥 |

## 🚀 本地跑起来（测试模式，不花钱）

1. 注册 Stripe：https://dashboard.stripe.com/register ，先不用激活账户，测试模式就能用。
2. 在 Dashboard → Developers → API keys 复制 **Secret key**（`sk_test_` 开头）。
3. 安装并配置：
   ```bash
   pip install -r requirements.txt
   cp .env.example .env      # Windows: copy .env.example .env
   ```
   把 `sk_test_...` 填进 `.env` 的 `STRIPE_SECRET_KEY`。
4. 安装 Stripe CLI（https://docs.stripe.com/stripe-cli ），然后：
   ```bash
   stripe login
   stripe listen --forward-to localhost:4242/webhook
   ```
   它会打印一个 `whsec_...`，填进 `.env` 的 `STRIPE_WEBHOOK_SECRET`。这个窗口保持开着。
5. 另开一个窗口：`python server.py`，打开 http://localhost:4242
6. 加购物车 → Checkout → 在 Stripe 页面用测试卡 `4242 4242 4242 4242`，有效期填未来任意日期，CVC 任意 3 位，邮编任意。
7. 付款后跳回成功页，显示 ✅ Paid。在 Stripe Dashboard → Payments 也能看到这笔测试付款。

想查所有订单：在 `.env` 设 `ADMIN_KEY=随便一个密码`，访问 `http://localhost:4242/admin/orders?key=那个密码`。

## 🔐 安全设计

- 价格只存在 `server.py` 里，浏览器只传商品 id 和数量，改网页改不了价格。
- 卡号只在 Stripe 的页面输入，你的服务器永远接触不到，所以不需要自己做 PCI 合规。
- 订单只有在 Stripe 发来**签名验证通过**的 Webhook 后才标记为已付款，成功页的跳转本身不算数。

## 🌐 上线收真钱

1. Stripe Dashboard 激活账户：填公司或个人信息、EIN 或 SSN、收款银行账户。
2. 部署后端到有 HTTPS 的主机（Render、Railway、Fly.io 都可以直接跑 Flask），生产环境用 `gunicorn server:app`。
3. `.env` 换成正式密钥 `sk_live_...`，`DOMAIN` 改成你的网址。
4. Dashboard → Developers → Webhooks 添加 `https://你的域名/webhook`，选事件 `checkout.session.completed` 和 `checkout.session.expired`，把它给的 `whsec_...` 填进 `.env`。
5. **销售税**：美国各州规则不同。在 Dashboard 开通 Stripe Tax，然后把 `server.py` 里 `automatic_tax` 那行的注释去掉。
6. **收据邮件**：Dashboard → Settings → Customer emails 打开 "Successful payments"。
7. 删掉或加强 `/admin/orders`，补上退货政策、隐私政策和联系方式页面。

## ✏️ 改商品

编辑 `server.py` 里的 `PRODUCTS`，价格单位是**美分**（`189900` = $1,899.00）。`art` 可选：`sofa` `chair` `table` `bed` `shelf` `lamp` `cabinet`。
运费在 `FREE_SHIPPING_CENTS`、`STANDARD_SHIPPING_CENTS`、`WHITE_GLOVE_CENTS` 里调。
