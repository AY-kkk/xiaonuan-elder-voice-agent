# 第二次修改 SPEC：端侧交互与角色生成重构

> 目标：在第一轮“老人端去复杂化、子女端承接配置”的基础上，进一步重构入口、老人端通话对象选择、子女端角色生成与底部导航信息架构。

---

## 0. 修改范围

本轮修改覆盖四个方向：

1. 新增入场登录界面：白色背景 + 橙色吉祥物动态画面 + 轻微阴影。
2. 老人端“换个声音/选择陪伴”命名改为“通话对象”，页面只负责选择角色。
3. 子女端新增“角色生成”栏，承接原老人端生成角色的所有交互组件与后端接口。
4. 子女端底部导航改为“点击选项后打开对应界面”，各功能不再堆在同一个长页面里。

---

## 1. 入场登录界面

### 1.1 产品目标

当前项目进入后直接选择身份或进入端侧页面，缺少品牌入场感。新增一个轻量登录/入场页，用橙色吉祥物建立产品温度。

### 1.2 页面定位

页面名称建议：

```text
小暖 · 欢迎页
```

文件建议：

```text
web/index.html
```

或拆出：

```text
web/login.html
```

如果保留现有 `web/index.html` 身份选择页，则建议把它升级为入场页。

### 1.3 视觉要求

背景：

```text
纯白或接近白色：#FFFFFF / #FFFDFC
```

吉祥物：

- 主体为当前橙色小暖吉祥物。
- 居中或略偏上。
- 有轻微投影，不要厚重。
- 可做轻微呼吸动效、眨眼、挥手。
- 不使用复杂渐变背景。

推荐 CSS：

```css
.entry {
  min-height: 100vh;
  background: #fffdfc;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.entry-mascot {
  filter: drop-shadow(0 14px 28px rgba(28, 26, 23, .14));
  animation: mascot-breathe 2.8s ease-in-out infinite;
}

@keyframes mascot-breathe {
  0%, 100% { transform: translateY(0) scale(1); }
  50% { transform: translateY(-6px) scale(1.025); }
}
```

### 1.4 交互流程

入口页只做两件事：

1. 展示品牌与吉祥物。
2. 让用户选择身份。

按钮：

```text
我是长辈
我是家人
```

进入路径：

```text
我是长辈 → /elder/
我是家人 → /parent/
```

### 1.5 文案建议

主标题：

```text
小暖在这里
```

副标题：

```text
一边陪长辈说话，一边帮家人放心
```

老人按钮：

```text
我是长辈
```

子女按钮：

```text
我是家人
```

### 1.6 验收标准

- 首屏背景为白色或近白色。
- 橙色吉祥物有动态呼吸/眨眼/挥手中的至少一种。
- 吉祥物有轻微阴影。
- 页面没有技术词。
- 老人和子女入口清晰。
- 移动端 390px 宽度下无横向溢出。

---

## 2. 老人端：“选择陪伴”改为“通话对象”

### 2.1 产品目标

“选择陪伴”仍有一点抽象，“通话对象”对老人更直接：我要和谁说话。

本轮统一命名：

```text
选择陪伴 → 通话对象
```

不要使用：

```text
换个声音
声音克隆
角色生成
人格
训练
speaker_id
```

### 2.2 底部导航调整

老人端当前建议：

```text
通话 / 关心我的事 / 选择陪伴 / 切换身份
```

改为：

```text
通话 / 关心我的事 / 通话对象 / 切换身份
```

涉及文件：

```text
web/elder/index.html
web/elder/memories.html
web/elder/character.html
```

### 2.3 页面职责

老人端 `web/elder/character.html` 页面只负责展示和选择：

- 默认小暖
- 子女端同步过来的角色

不允许出现：

- 创建角色表单
- 上传音频
- 填写音色代号
- 填写说话方式
- 查看训练状态
- 删除角色
- 同步按钮

### 2.4 页面结构

页面标题：

```text
通话对象
```

副标题：

```text
想和谁说话，就选谁
```

角色卡片示例：

```text
小暖
默认通话对象
我一直在，想聊就点我
[正在使用]
```

```text
女儿小芳
女儿
声音已经准备好了
[和她通话]
```

### 2.5 状态栏要求

用户提到“状态栏里只需要选择有哪些角色就好，其他所有都不用”。这里定义为：

老人端角色页只显示角色可选状态，不显示技术进度。

允许的状态：

```text
正在使用
可以通话
家人正在准备
```

不允许的状态：

```text
training
ready
synced
active
persona_ready
voice_status
```

如果角色没有完全准备好，老人端默认不展示；如必须展示，只能写：

```text
家人正在准备
```

### 2.6 API 对接

老人端只调用：

```http
GET /api/elder/{elder_id}/companions
POST /api/elder/{elder_id}/companions/{character_id}/activate
```

返回字段只使用：

```json
{
  "items": [
    {
      "id": 0,
      "name": "小暖",
      "relation": "默认通话对象",
      "ready": true,
      "is_active": true,
      "elder_copy": "我一直在，想聊就点我"
    }
  ]
}
```

### 2.7 验收标准

- 老人端底部导航出现“通话对象”。
- 老人端不出现“换个声音”。
- 老人端角色页不出现任何创建/上传/训练/同步交互。
- 老人端角色页只展示角色列表和选择按钮。
- 老人端页面无技术词。

---

## 3. 子女端：新增“角色生成”栏

### 3.1 产品目标

将原老人端“换个声音”里的角色生成能力完整迁移到子女端。子女端成为唯一角色生成与配置入口。

### 3.2 命名

子女端模块名称：

```text
角色生成
```

或更温和一点：

```text
准备通话对象
```

本轮按用户要求使用：

```text
角色生成
```

### 3.3 功能范围

角色生成栏包含：

1. 创建角色。
2. 上传授权录音。
3. 填写音色代号。
4. 填写说话方式。
5. 生成人物性格/说话方式提示词。
6. 查看准备状态。
7. 同步给老人端。
8. 设置当前通话对象。
9. 删除角色。

### 3.4 后端接口迁移

原有底层接口：

```http
/api/character/{elder_id}
```

应迁移到子女端路由语义：

```http
GET    /api/parent/{elder_id}/characters
POST   /api/parent/{elder_id}/characters
POST   /api/parent/{elder_id}/characters/{cid}/voice
GET    /api/parent/{elder_id}/characters/{cid}/voice
POST   /api/parent/{elder_id}/characters/{cid}/persona
POST   /api/parent/{elder_id}/characters/{cid}/sync
POST   /api/parent/{elder_id}/characters/{cid}/activate
DELETE /api/parent/{elder_id}/characters/{cid}
```

`/api/character` 可暂时保留兼容，但前端不再直接使用。

### 3.5 创建角色表单

字段：

```text
角色名字
和长辈的关系
长辈平时怎么称呼 TA
```

示例：

```text
女儿小芳
女儿
小芳 / 闺女
```

### 3.6 上传声音表单

字段：

```text
音色代号 S_xxx
录音文件
授权确认
```

授权确认文案：

```text
我确认已获得该声音本人或合法代理人的授权，仅用于陪伴本家庭老人。
```

### 3.7 生成人物性格表单

字段：

```text
TA 平时怎么称呼老人
TA 的语气
TA 常说的话
TA 不应该主动提起的话
```

按钮：

```text
生成说话方式
```

不要叫：

```text
人格蒸馏
prompt 生成
系统提示词
```

### 3.8 状态展示

子女端可以展示技术状态，但要翻译成人话：

| 后端状态 | 子女端文案 |
|---|---|
| voice_status=none | 声音未准备 |
| voice_status=training | 声音正在准备 |
| voice_status=ready | 声音已准备好 |
| voice_status=failed | 声音准备失败 |
| persona_status=none | 说话方式未生成 |
| persona_status=ready | 说话方式已生成 |
| sync_status=ready | 可以同步给老人端 |
| sync_status=synced | 老人端已可选择 |
| sync_status=active | 正在作为通话对象 |

### 3.9 页面路径建议

如果子女端改为多界面结构，角色生成建议独立页面：

```text
web/parent/characters.html
```

或 SPA 弹出层：

```text
parent/index.html#characters
```

本轮建议使用独立页面或弹出式 panel，不再把所有内容堆在 `parent/index.html` 的同一长页里。

### 3.10 验收标准

- 老人端不再有任何角色生成交互。
- 子女端有“角色生成”入口。
- 子女端能完成创建、上传声音、生成说话方式、同步给老人端。
- 老人端刷新后能看到同步后的通话对象。
- 后端接口全部从子女端路径调用。

---

## 4. 子女端底部导航：点击即打开对应界面

### 4.1 当前问题

当前子女端所有模块在同一个页面纵向堆叠：

```text
整体状态
充值与余额
每日信号
陪伴角色
重点事项
记忆审核
```

问题：

- 页面过长。
- 底部导航只是滚动定位，不是真正切换界面。
- 子女端看起来像后台管理页，缺少移动端应用感。

### 4.2 新交互目标

底部每个选项点击后，打开对应界面。

推荐底部导航：

```text
信号 / 事项 / 角色生成 / 充值 / 我的
```

点击行为：

| 导航 | 打开界面 |
|---|---|
| 信号 | 今日信号界面 |
| 事项 | 重点事项 + 记忆审核界面 |
| 角色生成 | 角色生成界面 |
| 充值 | 充值与余额界面 |
| 我的 | 身份切换 / 设置 |

### 4.3 技术实现方案 A：多页面

新增文件：

```text
web/parent/index.html          # 今日信号
web/parent/facts.html          # 事项与记忆审核
web/parent/characters.html     # 角色生成
web/parent/wallet.html         # 充值与余额
web/parent/profile.html        # 我的
```

优点：

- 简单清晰。
- 每页职责单一。
- 适合当前原生 HTML/JS 技术栈。

缺点：

- 公共导航和工具函数需要抽取，避免重复。

建议新增：

```text
web/parent/shared.js
web/parent/nav.js
web/parent/parent.css
```

### 4.4 技术实现方案 B：单页弹出式 panel

保留：

```text
web/parent/index.html
```

新增页面容器：

```html
<section class="view active" id="view-signals"></section>
<section class="view" id="view-facts"></section>
<section class="view" id="view-characters"></section>
<section class="view" id="view-wallet"></section>
<section class="view" id="view-profile"></section>
```

底部点击时：

```js
switchView("characters")
```

样式：

```css
.view { display: none; }
.view.active { display: block; }
```

如果用户明确说“弹出对应界面”，可以做成 bottom sheet：

```css
.sheet {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  max-height: 88vh;
  background: var(--color-card);
  border-radius: 24px 24px 0 0;
  transform: translateY(100%);
  transition: transform .25s ease;
}
.sheet.open {
  transform: translateY(0);
}
```

### 4.5 推荐方案

当前项目是原生 HTML/CSS/JS，没有前端路由框架。推荐：

```text
第一步使用多页面方案。
```

原因：

- 改动可控。
- 更符合“所有界面不应该放在同一个界面里”。
- 后续如果迁移到 Vue/React/Compose，也容易映射为多个 route。

### 4.6 页面职责拆分

#### `web/parent/index.html`

只保留：

- 今日状态
- 每日信号
- 最新提醒

#### `web/parent/facts.html`

包含：

- 重点事项
- 记忆审核
- 不主动提的话题

#### `web/parent/characters.html`

包含：

- 角色列表
- 创建角色
- 上传声音
- 生成说话方式
- 同步给老人端

#### `web/parent/wallet.html`

包含：

- 当前余额
- 预计陪伴分钟
- 充值套餐
- 计费说明
- 充值记录

#### `web/parent/profile.html`

包含：

- 当前家庭/长辈
- 身份切换
- 隐私说明
- 服务设置

### 4.7 底部导航实现

每个页面共用相同底部导航：

```html
<nav class="tabbar" aria-label="主导航">
  <a class="tab" href="./index.html">信号</a>
  <a class="tab" href="./facts.html">事项</a>
  <a class="tab" href="./characters.html">角色生成</a>
  <a class="tab" href="./wallet.html">充值</a>
  <a class="tab" href="./profile.html">我的</a>
</nav>
```

当前页高亮：

```html
<a class="tab active" href="./characters.html">角色生成</a>
```

### 4.8 验收标准

- 子女端底部导航不再只是滚动定位。
- 点击底部导航会打开对应独立界面。
- 每个界面只承担一个主要任务。
- `parent/index.html` 不再堆叠所有模块。
- 移动端体验像 App，而不是长网页后台。

---

## 5. 后端接口总览

### 5.1 老人端接口

```http
GET  /api/elder/{elder_id}/memories
GET  /api/elder/{elder_id}/companions
POST /api/elder/{elder_id}/companions/{character_id}/activate
POST /api/elder/{elder_id}/companions/{character_id}/notice_seen
```

### 5.2 子女端接口

信号：

```http
GET /api/parent/{elder_id}/signals
```

事项：

```http
GET    /api/parent/{elder_id}/key_facts
POST   /api/parent/{elder_id}/key_facts
PATCH  /api/parent/{elder_id}/key_facts/{fact_id}
DELETE /api/parent/{elder_id}/key_facts/{fact_id}
GET    /api/parent/{elder_id}/memories
PATCH  /api/parent/{elder_id}/memories/{memory_id}
```

角色生成：

```http
GET    /api/parent/{elder_id}/characters
POST   /api/parent/{elder_id}/characters
POST   /api/parent/{elder_id}/characters/{cid}/voice
GET    /api/parent/{elder_id}/characters/{cid}/voice
POST   /api/parent/{elder_id}/characters/{cid}/persona
POST   /api/parent/{elder_id}/characters/{cid}/sync
POST   /api/parent/{elder_id}/characters/{cid}/activate
DELETE /api/parent/{elder_id}/characters/{cid}
```

充值：

```http
GET  /api/parent/{elder_id}/wallet
POST /api/parent/{elder_id}/recharge
```

---

## 6. 数据结构影响

### 6.1 characters 表

继续使用当前字段：

```text
voice_status
persona_status
sync_status
is_active
synced_at
elder_notice_seen_at
elder_alias
```

本轮不需要新增字段。

### 6.2 wallet 表

继续使用：

```text
wallet_accounts
wallet_transactions
billing_rules
```

本轮主要改前端页面拆分，不需要新增钱包字段。

---

## 7. 实施顺序

### Step 1：入口页

1. 改造 `web/index.html`。
2. 加入白底橙色吉祥物动效。
3. 保留“我是长辈 / 我是家人”两个入口。

### Step 2：老人端命名与页面简化

1. 全局替换老人端“选择陪伴”为“通话对象”。
2. 确认老人端不出现“换个声音”。
3. 确认 `web/elder/character.html` 只展示角色列表。

### Step 3：子女端拆页

1. 从 `web/parent/index.html` 拆出：
   - `facts.html`
   - `characters.html`
   - `wallet.html`
   - `profile.html`
2. 抽取公共 JS：
   - `shared.js`
   - `nav.js`
3. 抽取公共 CSS：
   - `parent.css`

### Step 4：角色生成迁移

1. 把角色生成 UI 放入 `web/parent/characters.html`。
2. 确保所有角色接口调用 `/api/parent/{elder_id}/characters...`。
3. 老人端不再调用 `/api/character`。

### Step 5：验证

1. 打开入口页。
2. 进入老人端。
3. 确认底部为“通话对象”。
4. 进入子女端。
5. 底部点击“角色生成”打开独立角色生成界面。
6. 创建角色并同步给老人端。
7. 老人端通话对象页出现该角色。

---

## 8. 测试清单

### 8.1 自动测试

继续运行：

```bash
.venv/bin/python -m backend.scripts.test_character
.venv/bin/python -m backend.scripts.test_parent_api
.venv/bin/python -m backend.scripts.test_e2e
.venv/bin/python -m backend.scripts.test_usage
.venv/bin/python -m backend.scripts.test_privacy
```

### 8.2 页面测试

检查页面：

```text
/index.html
/elder/
/elder/character.html
/parent/
/parent/facts.html
/parent/characters.html
/parent/wallet.html
/parent/profile.html
```

### 8.3 文案扫描

老人端不得出现：

```text
换个声音
克隆
训练
speaker_id
蒸馏
token
成本
```

子女端不得出现：

```text
人格蒸馏
prompt
系统提示词
```

子女端允许出现：

```text
音色代号
角色生成
充值
计费说明
```

---

## 9. 最终验收标准

1. 有新的入场登录界面。
2. 入场页白色背景、橙色吉祥物动态、有轻微阴影。
3. 老人端底部导航为：

```text
通话 / 关心我的事 / 通话对象 / 切换身份
```

4. 老人端“通话对象”页只展示角色，不包含任何生成角色功能。
5. 子女端底部导航为：

```text
信号 / 事项 / 角色生成 / 充值 / 我的
```

6. 子女端每个底部选项打开独立界面。
7. 角色生成全部迁移到子女端。
8. 子女端角色生成调用 parent API。
9. 子女端同步角色后，老人端可以选择该通话对象。
10. 移动端无横向溢出，无控制台错误。
