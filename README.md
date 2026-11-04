# 🪵 Mujian Furniture

美国家具网店 + 发货管理后台。Flask + SQLite，付款走 Stripe Checkout。

| 页面 | 地址 | 说明 |
|---|---|---|
| 🛍️ 商店 | `/` | 顾客选家具、看 360° 旋转图、结账 |
| 🧭 后台 | `/admin.html` | 订单、发货、打印、商品、360 图（中文界面） |
| 🧾 打印 | `/print.html` | 装箱单和 4×6 地址标签，由后台打开 |

## ✨ 功能

**📦 订单**
- 状态流转：🟠 待发货 → 🚚 已发货 → ✅ 已送达，也可以取消（库存自动退回）
- 填快递公司和单号（USPS / UPS / FedEx / 物流专车），自动生成物流查询链接
- 标记已发货时自动给顾客发通知邮件（需配置 SMTP，没配置就只记录）
- 每笔订单都有操作记录、后台备注、一键复制地址
- 「🧪 生成测试订单」：不用付款也能模拟一笔真实订单

**🖨️ 打印**
- 🧾 装箱单：Letter 尺寸，普通打印机，放进箱子给顾客
- 🏷️ 地址标签：4×6 英寸，带订单号条码，适合 Rollo / Zebra 等热敏标签打印机
- 可以勾选多个订单批量打印
- ⚠️ 地址标签**不含邮资**。邮资在 USPS / Pirate Ship / UPS 网站购买

**🪑 商品**
- 新增、修改、下架、删除商品；改价格和库存
- 上传主图（自动转成 JPG、压缩、修正手机照片方向）
- 库存在付款后自动扣减，库存不足时不能下单，商店会显示 "Only N left" / "Sold out"

**🔄 360° 旋转图**
1. 家具放在转盘中央，手机固定在三脚架上不动
2. 每转 10°–15° 拍一张，一圈 24–36 张。白墙或纯色背景，光线均匀
3. 后台 → 商品 → 选择这一组照片。浏览器会自动按文件名排序、找出家具位置、统一裁切居中、把背景处理成白色
4. 先拖动预览效果，满意再点保存。商店商品页就能拖动旋转

支持 JPG / PNG / WebP。iPhone 的 HEIC 照片要先导出成 JPG（相机设置 → 格式 → 兼容性最佳）。

## 🚀 本地运行

```bash
pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env
```

打开 `.env`，至少设置 `ADMIN_PASSWORD`。然后：

```bash
python server.py
```

- 商店：http://localhost:4242
- 后台：http://localhost:4242/admin.html

**没填 Stripe 密钥时是演示模式**：商店里点 Checkout 会直接生成一笔已付款的测试订单，适合先熟悉后台流程。

### 💳 接上 Stripe 测试付款

1. 注册 https://dashboard.stripe.com/register ，Developers → API keys 复制 `sk_test_...` 填到 `STRIPE_SECRET_KEY`
2. 安装 Stripe CLI（https://docs.stripe.com/stripe-cli ），运行：
   ```bash
   stripe login
   stripe listen --forward-to localhost:4242/webhook
   ```
   把它打印的 `whsec_...` 填到 `STRIPE_WEBHOOK_SECRET`，重启 `server.py`
3. 结账时用测试卡 `4242 4242 4242 4242`，有效期填未来任意日期，CVC 任意 3 位

## 🔐 安全设计

- 价格和库存只以服务器数据库为准，浏览器只传商品编号和数量
- 卡号只在 Stripe 的页面输入，服务器接触不到
- 订单只有收到**签名验证通过**的 Stripe Webhook 才算已付款
- 后台用密码登录，登录状态存在签名的 HttpOnly Cookie 里
- 上传的图片都经过 Pillow 重新编码，非图片文件会被拒绝

## 🌐 上线

1. **主机**：Render / Railway / Fly.io，启动命令 `gunicorn server:app`
2. **数据持久化**：数据库 `shop.db` 和图片 `uploads/` 需要放在持久磁盘上（Render 要加 Disk，挂载后用 `DB_PATH`、`UPLOAD_DIR` 指过去），否则每次重新部署会丢数据
3. **环境变量**：`ADMIN_PASSWORD` 设强密码，`SECRET_KEY` 设随机字符串，`DOMAIN` 改成 `https://你的域名`
4. **Stripe**：激活账户，换成 `sk_live_...`；Dashboard → Webhooks 添加 `https://你的域名/webhook`，事件选 `checkout.session.completed` 和 `checkout.session.expired`
5. **销售税**：开通 Stripe Tax，去掉 `server.py` 里 `automatic_tax` 那行的注释
6. **邮件**：填 `SMTP_*`。Gmail 用 `smtp.gmail.com`、端口 587、应用专用密码
7. 补上退货政策、隐私政策、联系方式页面

## 🗂️ 文件

```
server.py                 后端：商店 API、Stripe、后台 API、上传
public/
  index.html success.html 商店、订单确认
  admin.html admin.css    后台
  print.html              装箱单 / 地址标签
  api.js                  前端调用后端的接口
  spin.js                 360 照片处理 + 拖动旋转查看器
  art.js style.css        插图和商店样式
  demo-spin/tv1/          示例 360 图（电视柜）
tools/
  render_demo_spin.py     生成示例 360 图
  preview/                免服务器预览版（数据存在浏览器）：python tools/preview/build_preview.py 输出目录
```
