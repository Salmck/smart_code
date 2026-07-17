# 部署到公网

> 当前 Claude 运行的容器是隔离临时环境、无公网 IP 且出网受限，**无法从这里直接对外暴露**。
> 下面三种方式任选其一，即可让系统真正外网可访问。代码已推送到你的仓库，随处 clone 即可。

## 方式一：云主机 + Docker（推荐，最接近生产）

在你自己的云主机（阿里云 ECS / 腾讯云 CVM / 任意 VPS，已有公网 IP）上：

```bash
git clone <你的仓库地址> ordinex && cd ordinex
docker build -t ordinex .
docker run -d --name ordinex -p 80:8000 \
  -e JWT_SECRET="$(openssl rand -hex 32)" \
  -e DYNCODE_SECRET="$(openssl rand -hex 32)" \
  -v ordinex-data:/app/data \
  ordinex
```

然后浏览器访问 `http://你的公网IP/login`。记得在云厂商安全组放行对应端口。

**强烈建议加 HTTPS**：店员端摄像头扫码（`getUserMedia`）在非 HTTPS 下会被浏览器禁用，
微信内置浏览器也要求 HTTPS。最省事的方式是用 Caddy 自动签发证书：

```bash
# 有域名后，一个 Caddyfile 即可自动 HTTPS
# your-domain.com {
#     reverse_proxy localhost:8000
# }
docker run -d -p 80:80 -p 443:443 -v ./Caddyfile:/etc/caddy/Caddyfile caddy
```

## 方式二：PaaS 一键部署（最快拿到公网 URL）

Railway / Render / Fly.io 等会自动识别 Dockerfile 并**自动分配带 HTTPS 的公网域名**：

1. 把仓库连接到平台
2. 平台读取 `Dockerfile` 自动构建
3. 在环境变量里设置 `JWT_SECRET`、`DYNCODE_SECRET`（生产必改）
4. 拿到形如 `https://ordinex-xxx.up.railway.app` 的公网地址

注意 SQLite 在部分 PaaS 的临时磁盘上不持久，正式使用请挂持久卷或切 PostgreSQL
（设 `DATABASE_URL=postgresql+psycopg://…`，代码无需改）。

## 方式三：本机运行 + 自建隧道（临时演示最快）

在**你自己的电脑**（不受本容器代理限制）上跑起来后，用隧道临时对外：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 另开一个终端
cloudflared tunnel --url http://localhost:8000    # 或 ngrok http 8000
```

隧道工具会给你一个临时公网 URL，适合快速演示，不适合长期使用。

---

## 生产必改清单

| 项 | 设置 |
|----|------|
| JWT 密钥 | `JWT_SECRET` 强随机 |
| 动态码密钥 | `DYNCODE_SECRET` 强随机 |
| 关闭验证码回显 | `DEBUG=false`（Dockerfile 已默认） |
| 真实短信 | `SMS_PROVIDER=aliyun/tencent`，实现 `app/sms.py` 对应分支 |
| 真实人机验证 | `CAPTCHA_PROVIDER=…`，实现 `app/captcha.py` |
| 数据库 | 多实例部署换 `DATABASE_URL` 为 PostgreSQL |
| 限流存储 | 多实例部署把 `app/store.py` 换成 Redis |
| HTTPS | 摄像头扫码与微信浏览器必需 |
