# Ordinex 一键启动（Windows PowerShell）
# 同时启动 cloudflared 隧道 + 服务，自动抓取公网地址填入 PUBLIC_BASE_URL。
# 用法：powershell -ExecutionPolicy Bypass -File run_local.ps1
# 前提：已安装 cloudflared，并已激活 Python 环境（conda activate ordinex 或 venv）

$ErrorActionPreference = "Stop"
$Port = 8000

# -1) 自动拉取最新代码（拉了代码不重启会出现「页面新、接口旧」的 404/422）
if (Test-Path .git) {
    Write-Host "git pull ..." -ForegroundColor Cyan
    git pull --ff-only
}

# 0) 清理占用端口的残留进程（旧进程跑旧代码会导致新功能 404/422）
$stale = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($stale) {
    $stale | ForEach-Object {
        Write-Host "端口 $Port 被 PID $($_.OwningProcess) 占用，正在结束旧进程 ..." -ForegroundColor Yellow
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

# 1) 检查 cloudflared
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "未找到 cloudflared，请先安装：winget install Cloudflare.cloudflared" -ForegroundColor Red
    exit 1
}

# 2) 后台启动隧道，日志写入 cf.log（cloudflared 的地址打印在 stderr）
Remove-Item cf.log -ErrorAction SilentlyContinue
$cf = Start-Process cloudflared -ArgumentList "tunnel","--url","http://localhost:$Port" -RedirectStandardError "cf.log" -PassThru -WindowStyle Hidden
Write-Host "cloudflared 已启动 (PID $($cf.Id))，等待分配公网地址 ..."

# 3) 轮询日志抓取 trycloudflare 地址（最多等 30 秒）
$url = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path cf.log) {
        $m = Select-String -Path cf.log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value; break }
    }
}
if (-not $url) {
    Write-Host "30 秒内未拿到隧道地址，请查看 cf.log 排错" -ForegroundColor Red
    Stop-Process -Id $cf.Id -ErrorAction SilentlyContinue
    exit 1
}

# 4) 设置公网地址（二维码/短链会指向它）并启动服务
$env:PUBLIC_BASE_URL = $url
Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  公网地址:  $url/login" -ForegroundColor Green
Write-Host "  账号: admin/admin123  boss/boss123  staff/staff123"
Write-Host "  按 Ctrl+C 停止（会顺带结束隧道）"
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

try {
    uvicorn app.main:app --host 0.0.0.0 --port $Port --proxy-headers
}
finally {
    Stop-Process -Id $cf.Id -ErrorAction SilentlyContinue
    Write-Host "已停止隧道"
}
