#!/usr/bin/env bash
# Ordinex 一键启动（Mac / Linux）
# 同时启动 cloudflared 隧道 + 服务，自动抓取公网地址填入 PUBLIC_BASE_URL。
# 用法：bash run_local.sh   （前提：已装 cloudflared，已激活 Python 环境）
set -euo pipefail
PORT="${PORT:-8000}"

# 自动拉取最新代码（拉了代码不重启会出现「页面新、接口旧」的 404/422）
[ -d .git ] && { echo "git pull ..."; git pull --ff-only || true; }

# 清理占用端口的残留进程（旧进程跑旧代码会导致新功能 404/422）
if command -v lsof >/dev/null 2>&1; then
  STALE=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
  if [ -n "$STALE" ]; then
    echo "端口 $PORT 被占用（PID $STALE），结束旧进程 ..."
    kill -9 $STALE 2>/dev/null || true
    sleep 1
  fi
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "未找到 cloudflared。安装：brew install cloudflared（Mac）或见 https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  exit 1
fi

rm -f cf.log
cloudflared tunnel --protocol http2 --url "http://localhost:${PORT}" > cf.log 2>&1 &
CF_PID=$!
trap 'kill $CF_PID 2>/dev/null || true' EXIT
echo "cloudflared 已启动 (PID $CF_PID)，等待分配公网地址 ..."

URL=""
for _ in $(seq 1 30); do
  sleep 1
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' cf.log | head -1 || true)
  [ -n "$URL" ] && break
done
if [ -z "$URL" ]; then
  echo "30 秒内未拿到隧道地址，请查看 cf.log 排错"
  exit 1
fi

export PUBLIC_BASE_URL="$URL"
echo ""
echo "=============================================="
echo "  公网地址:  $URL/login"
echo "  账号: admin/admin123  boss/boss123  staff/staff123"
echo "  按 Ctrl+C 停止（会顺带结束隧道）"
echo "=============================================="
echo ""

uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers
