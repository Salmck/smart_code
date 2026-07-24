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
