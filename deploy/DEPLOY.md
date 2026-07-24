# 部署到自有服务器（nginx + uvicorn + systemd）

以域名 `wujexu.com`、部署目录 `/opt/ordinex` 为例。目录换成你的实际路径即可。

## 0. 前提
- 已有服务器（Ubuntu/Debian 示例），已装 `nginx`、`python3`、`certbot`
- 域名 `wujexu.com`、`www.wujexu.com` 的 A 记录已指向本机公网 IP

## 1. 拉代码 + 建虚拟环境
```bash
sudo mkdir -p /opt/ordinex && sudo chown "$USER" /opt/ordinex
git clone <你的仓库地址> /opt/ordinex
cd /opt/ordinex
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. 配置环境变量（含密钥）
```bash
cp deploy/ordinex.env.example deploy/ordinex.env
# 生成两个密钥并填进去
openssl rand -hex 32   # → 填 JWT_SECRET
openssl rand -hex 32   # → 填 DYNCODE_SECRET
nano deploy/ordinex.env
chmod 600 deploy/ordinex.env
```
确认 `DEBUG=false`、`PUBLIC_BASE_URL=https://wujexu.com`。

## 3. 初始化数据（首次）
```bash
# 首次启动会自动建表并灌演示数据；生产不想要演示数据可跳过，直接在后台创建门店
set -a; . deploy/ordinex.env; set +a
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
# 看到 Uvicorn running 就 Ctrl+C 停掉，改用 systemd 托管
```

## 4. systemd 托管（开机自启 + 崩溃重启）
```bash
# 编辑 deploy/ordinex.service，把 User/Group/WorkingDirectory/路径 改成你的
sudo cp deploy/ordinex.service /etc/systemd/system/ordinex.service
sudo systemctl daemon-reload
sudo systemctl enable --now ordinex
systemctl status ordinex          # 看是否 running
journalctl -u ordinex -f          # 实时日志
```
> `WorkingDirectory` 必须是项目根目录：SQLite 库 `./ordinex.db` 与上传目录 `./data/uploads`
> 都是相对路径，目录不对会连到空库、图片 404。确保 `User` 对该目录有读写权限。

## 5. nginx 反代
```bash
sudo cp deploy/nginx-wujexu.conf /etc/nginx/sites-available/wujexu.com
sudo ln -s /etc/nginx/sites-available/wujexu.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 6. 签发 HTTPS 证书
```bash
sudo certbot --nginx -d wujexu.com -d www.wujexu.com
# 自动续期已由 certbot 定时任务处理，无需手动
```

## 7. 验证
- 浏览器打开 `https://wujexu.com` → 后台登录页
- 后台建活动、审核通过 → 二维码内容应为 `https://wujexu.com/q/xxxx`
- 手机扫码 → 落地页正常、能上传图片（>1MB 不报 413）

## 升级发版（数据不丢）
```bash
cd /opt/ordinex
git pull
.venv/bin/pip install -r requirements.txt   # 依赖有变时
sudo systemctl restart ordinex
```
`DEBUG=false` 下即使模型加了新表/新列，启动时只做**非破坏式迁移**（补表补列），
现有门店/凭证/核销数据全部保留。改列类型、删列这类需重建表的变更不在自动范围内，
遇到时需手动处理（或引入 Alembic）。

## 开启真实短信验证码（阿里云）

默认 `SMS_PROVIDER=mock`，验证码只打到日志。要真实发短信：

1. **阿里云短信控制台**准备三样（备案通过后才能申请国内短信）：
   - **短信签名**（如「无界序」）→ 审核通过
   - **验证码模板**，内容形如 `您的验证码是${code}，5分钟内有效，请勿泄露。` → 审核通过，记下模板 CODE（`SMS_xxxxxxxx`）
   - **AccessKey**（建议用子账号 RAM，只授予 `AliyunDysmsFullAccess`）
2. 填进 `deploy/ordinex.env`：
   ```
   SMS_PROVIDER=aliyun
   ALIYUN_SMS_ACCESS_KEY_ID=...
   ALIYUN_SMS_ACCESS_KEY_SECRET=...
   ALIYUN_SMS_SIGN_NAME=无界序
   ALIYUN_SMS_TEMPLATE_CODE=SMS_xxxxxxxx
   ALIYUN_SMS_TEMPLATE_PARAM=code        # 与模板里 ${code} 的变量名一致
   ```
3. 重启：`sudo systemctl restart ordinex`
4. 手机实测收码。失败时看日志定位：`journalctl -u ordinex -f`
   - `isv.BUSINESS_LIMIT_CONTROL` = 触发频控（同号 1 条/分、5 条/天等，阿里云侧默认限制）
   - `isv.MOBILE_NUMBER_ILLEGAL` = 号码格式非法
   - `isv.SMS_SIGNATURE_ILLEGAL` / `isv.SMS_TEMPLATE_ILLEGAL` = 签名或模板 CODE 不对

> 防刷：发码接口已有限流（同号 60s 冷却、5 条/小时，同 IP 20 条/小时）+ 人机验证。
> 人机验证目前仍是内置算术题（mock），若上线后遭遇恶意刷短信、担心话费，
> 可后续接入阿里云验证码 2.0 或 Cloudflare Turnstile（`CAPTCHA_PROVIDER` 已预留切换点）。

## 数据备份（重要）
SQLite 全部数据就在一个文件里，定时备份它 + 上传目录即可：
```bash
# 加到 crontab：每天凌晨 3 点备份
0 3 * * * sqlite3 /opt/ordinex/ordinex.db ".backup '/opt/ordinex/backup/ordinex-$(date +\%F).db'"
```
（用 `.backup` 而非 `cp`，避免 WAL 模式下拷到不一致状态。）

## 常见坑
| 现象 | 原因 | 解决 |
|---|---|---|
| 浏览器无限跳转 / 打不开 | uvicorn 没加 `--proxy-headers` | service 里已带，确认生效 |
| 上传图片报 413 | nginx 默认限 1MB | `client_max_body_size 10M`（已在 conf） |
| 二维码指向 localhost | 没设 `PUBLIC_BASE_URL` | env 里设成 `https://wujexu.com` 后重启 |
| 图片 404 / 数据是空的 | 启动目录不对 | `WorkingDirectory` 指向项目根目录 |
| 重启后数据没了 | 旧版本 DEBUG 未关会重建库 | 确认 `DEBUG=false`（已修复为非破坏迁移） |
