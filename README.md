# Ordinex 专属增长码系统（无界序）

以二维码为营销归因入口、以预约和一次性核销为成交结果的**门店增长追踪系统**。
首期服务餐饮门店，可扩展到美容、健身、宠物、文旅等线下行业。

系统核心是回答一个问题：

> 某家门店的某个增长策略，通过某条内容、某个渠道，最终带来了多少真实预约和核销？

核心数据链路（每一步都能反查上一步，归因链不断）：

```
门店 → 增长动作 → 营销活动 → 专属二维码 → 顾客扫码/行为
    → 预约/领取 → 一次性凭证 → 门店原子核销 → 渠道归因与增长判断
```

当前为**测试模式**：短信验证码打印到控制台、人机验证用算术题、SQLite 零配置，
克隆下来即可跑通全部业务闭环，无需等待任何外部资质审核。

---

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

首次启动自动灌入演示数据。打开 http://127.0.0.1:8000

### 测试入口与账号

| 端 | 地址 | 账号 |
|----|------|------|
| 后台登录（三端共用） | `/login` | 见下 |
| 平台管理端 | `/admin` | admin / admin123 |
| 门店老板端 | `/owner` | boss / boss123 |
| 店员核销端 | `/staff` | staff / staff123 |
| 顾客扫码入口 | `/admin/qrs` 里点「预览」，或扫描下载的二维码 | 无需账号，验证手机号即可 |

> 顾客手机验证码在**测试模式**下打印到服务端控制台，DEBUG 模式还会自动填入输入框。

### 一分钟体验完整闭环

1. 用 `admin` 登录 → `/admin/qrs` 看到 3 个演示二维码 → 点任一「预览」进入顾客套餐页
2. 点「立即预约」→ 输入手机号 → 完成人机验证 → 获取验证码（看控制台）→ 验证 → 填预约 → 提交
3. 得到核销凭证页（此时状态「待生效」，因演示门店为人工确认预约）
4. 用 `boss` 登录 → `/owner/reservations` → 点「确认」→ 凭证转为「可使用」并扣减库存
5. 用 `staff` 登录 → `/staff` → 手动输入凭证编号（或扫码）→「查询凭证」→「确认核销」
6. 再次核销同一凭证 → 系统明确拒绝「该凭证已于…核销，不可重复使用」
7. 回 `/owner` 或 `/admin` 看漏斗数据、`/owner/channels` 看渠道效果、`/admin/actions/{id}` 看增长判定

---

## 架构

模块化单体，三层清晰分离，便于换前端 / 加小程序 / 换数据库：

```
routers/   仅处理 HTTP（解析参数、调 service、渲染或返回 JSON）
services/  纯业务逻辑（不 import FastAPI，收纯参数返纯数据）
基础设施    provider 接口（sms / captcha / storage / notify / 限流存储）
```

| 目录/文件 | 职责 |
|-----------|------|
| `app/models.py` | 全部数据表（12 张） |
| `app/db.py` | SQLite（WAL）连接，换 PG 只改 `DATABASE_URL` |
| `app/deps.py` | 双通道认证（Cookie + Bearer）+ 门店数据隔离 |
| `app/services/redemption_service.py` | **原子核销**（系统最高风险模块） |
| `app/services/claim_service.py` | 领取 / 预约，原子扣库存 |
| `app/services/stats_service.py` | 漏斗 / 渠道 / 内容统计（只读 Event 表） |
| `app/services/growth_service.py` | 增长动作自动判定 |
| `app/attribution.py` | 归因上下文值对象（归因链不断的结构保证） |
| `app/statemachine.py` | 集中的状态机（凭证/预约/活动流转） |
| `app/dyncode.py` | 动态核销码（HMAC 签名，60 秒刷新） |
| `templates/customer/` | 顾客端（浅色，突出菜品与信任） |
| `templates/console/` | 经营三端（深色专业风） |

## 防重复核销（核心机制）

一次性核销由三重机制共同保证，`e2e.py` 用 5 并发请求实测「只成功一次」：

1. **条件原子 UPDATE**：`WHERE status='usable' AND expires_at>now`，只有命中一行才算成功
2. **`Redemption.voucher_id` 唯一约束**：一凭证一条成功核销记录
3. **`request_id` 唯一约束 + 幂等重放**：同一请求重复提交返回首次结果

即使两名店员同时扫、连点确认、网络重发、顾客截图转发，也只成功一次。

## 已实现的安全措施

- 二维码随机 base62 短码，链接不含门店/活动/顾客 ID，无连续编号
- 手机号默认脱敏展示（`138****8000`），后台不显示完整号码
- 验证码限流：60s 冷却、每手机号每小时 5 次、每 IP 每小时 20 次、错 5 次锁 15 分钟
- 严格门店数据隔离：老板/店员的 `store_id` 一律取自登录令牌，忽略任何请求参数
- 店员离职即时禁用（门店成员关系失效则请求 401）
- 关键操作全部写审计日志；活动/预约/核销数据软删除，不物理删除
- 动态核销码定期失效；二维码暂停后显示友好提示页
- 表单输入格式校验与基础清洗

## 第一版范围与限制

- **一活动一套餐、一活动一路径**（需预约的只走预约、免预约的只走领取）——README 明确的第一版约束
- 预约为**申请制**，非实时时段容量订位
- 冲正（错误核销纠错）已实现数据结构与管理员入口，凭证可恢复可用
- 暂不含：在线支付、退款、会员积分、储值、拼团、分销、商城、外卖、平台数据抓取

## 从测试模式切到生产

改环境变量（推荐），并在对应 provider 里实现真实分支：

| 要做的事 | 怎么改 |
|----------|--------|
| 关闭验证码回显 | `DEBUG=false` |
| 换 PostgreSQL | `DATABASE_URL=postgresql+psycopg://…`（SQLAlchemy 写法通用） |
| 换真实短信 | `SMS_PROVIDER=aliyun/tencent/twilio`，实现 `app/sms.py` 对应分支 |
| 换真实人机验证 | `CAPTCHA_PROVIDER=…`，实现 `app/captcha.py`，前端换厂商 SDK |
| 限流存储换 Redis | 替换 `app/store.py` 读写（多实例部署必须） |
| 文件存储换对象存储 | `STORAGE_PROVIDER=oss/cos` |
| 设置密钥 | `JWT_SECRET`、`DYNCODE_SECRET` 用强随机值 |

> 提醒：国内真实短信需企业实名 + 短信签名/模板审核（通常 1~3 天），这是上线前最耗时的一步。

**部署到公网**：见 [DEPLOY.md](DEPLOY.md) —— 云主机 + Docker、PaaS 一键部署、或本机自建隧道三种方式，含 HTTPS 与生产必改清单。

## 未来接入小程序

后端 service 层原生化零改动（已预留两个接缝）：

- **认证双通道**：`Authorization: Bearer` 与 Cookie 都认，小程序 `wx.request` 直接带 token
- **顾客接口可返回 JSON**：SSR 页面与 JSON API 调同一 service，模板只是其中一种「皮」

原生小程序 = 重写顾客端页面（约 10 个）+ 一层薄 JSON API，业务逻辑不动。

## 端到端验收

```bash
python scripts/e2e.py   # 见提交说明；覆盖 PRD 第十六节 13 条闭环 + 并发核销
```
