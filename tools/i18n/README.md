# 三种语言（中文 / English / Español）

- `public/i18n.js` 是生成出来的，不要直接改。改翻译就改这里的表，然后运行：
  ```bash
  python tools/i18n/build_i18n.py
  ```
- `admin_tr.py`：后台文字（中文原文 → 英文、西班牙文），顺序和 `keys2.json` 一一对应
- `store_tr.py`：商店文字（英文原文 → 中文、西班牙文），顺序和 `enkeys.json` 对应；`EXTRA` 里是带变量的句子
- `server_tr.py`：服务器返回的中文提示和订单记录的翻译，以及带变量的匹配规则
- 页面里新加文字：后台写中文、商店写英文，都用 `t("文字")` 包起来，再把翻译加到 `EXTRA` 或对应的表里
- 服务器返回的消息显示时用 `tr(消息)`，会自动按规则翻译
- `wrap.js` / `rename.js` 是当初批量加 `t()` 用的脚本（需要 `npm install acorn @babel/parser @babel/traverse`）
