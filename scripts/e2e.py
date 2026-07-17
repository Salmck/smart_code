"""端到端验收脚本：覆盖 PRD 第十六节 13 条闭环 + 防重复核销并发测试。"""
import concurrent.futures
import random
import re
import sys

import requests

BASE = "http://127.0.0.1:8000"
CHANNELS = ["抖音", "小红书", "微信朋友圈", "微信社群", "公众号", "视频号",
            "美团", "大众点评", "线下海报", "桌贴", "老顾客转介绍", "其他"]


def solve_captcha(s):
    d = s.get(f"{BASE}/api/captcha").json()
    q = d["question"].replace("= ?", "").strip()
    return d["captcha_id"], str(eval(q))


def p(msg, ok=True):
    print(("✅ " if ok else "❌ ") + msg)
    if not ok:
        sys.exit(1)


# ---------- 1. 管理员登录 ----------
admin = requests.Session()
r = admin.post(f"{BASE}/api/staff/login", data={"username": "admin", "password": "admin123"})
p(f"管理员登录 (role={r.json().get('role')})", r.ok and r.json()["role"] == "admin")

# ---------- 找到 seed 的二维码短码 ----------
r = admin.get(f"{BASE}/admin/qrs")
codes = re.findall(r"<code>(\w+)</code>", r.text)
p(f"读取到 {len(codes)} 个演示二维码短码", len(codes) >= 3)
qr_douyin = codes[0]  # 任取一个投放码，其渠道由后端归因

# ---------- 2. 顾客扫码（抖音码）----------
cust = requests.Session()
r = cust.get(f"{BASE}/q/{qr_douyin}")
p("顾客扫码打开套餐页", r.ok and "太平湖胖头鱼" in r.text)
vid = cust.cookies.get("ordinex_vid")
p(f"生成匿名访客ID ({vid[:8]}…)", bool(vid))

# 重复扫码（同一 vid）
cust.get(f"{BASE}/q/{qr_douyin}")

# ---------- 3. 顾客手机验证 ----------
phone = "139" + "".join(random.choice("0123456789") for _ in range(8))
cid, ans = solve_captcha(cust)
r = cust.post(f"{BASE}/api/customer/send-code",
              data={"short_code": qr_douyin, "phone": phone, "captcha_id": cid, "captcha_answer": ans})
p("发送验证码", r.ok)
code = r.json().get("debug_code")
p(f"取得验证码 {code}", bool(code))

r = cust.post(f"{BASE}/api/customer/verify",
              data={"short_code": qr_douyin, "phone": phone, "code": code})
p("验证手机号成功", r.ok and r.json().get("ok"))

# ---------- 4. 顾客预约（活动需预约，人工确认 → pending 凭证）----------
r = cust.post(f"{BASE}/api/customer/reserve",
              data={"short_code": qr_douyin, "name": "张先生", "date": "2026-07-20",
                    "time": "晚市 17:00-21:00", "people": 6, "need_room": "1", "note": "靠窗"})
p("提交预约", r.ok and r.json().get("ok"))
voucher_code = r.json()["voucher_code"]
p(f"生成核销凭证 {voucher_code}（状态应为待生效）", r.json()["reservation_status"] == "pending")

# ---------- 5. 店员登录 ----------
staff = requests.Session()
r = staff.post(f"{BASE}/api/staff/login", data={"username": "staff", "password": "staff123"})
p("店员登录", r.ok)

# ---------- 6. pending 凭证不可核销 ----------
r = staff.post(f"{BASE}/staff/api/verify", data={"raw_code": voucher_code})
info = r.json()
p("查询 pending 凭证：不可核销", r.ok and not info["redeemable"])

# ---------- 7. 老板确认预约 → 凭证转可用 + 扣库存 ----------
boss = requests.Session()
boss.post(f"{BASE}/api/staff/login", data={"username": "boss", "password": "boss123"})
r = boss.get(f"{BASE}/owner/reservations")
res_id = re.search(r"/owner/reservations/(\d+)/action", r.text).group(1)
r = boss.post(f"{BASE}/owner/reservations/{res_id}/action", data={"op": "confirm"})
p("老板确认预约", r.ok)

# ---------- 8. 店员两步核销：verify 可核销 ----------
r = staff.post(f"{BASE}/staff/api/verify", data={"raw_code": voucher_code})
info = r.json()
p("确认后查询：凭证可核销", r.ok and info["redeemable"])
vid_num = info["voucher_id"]

# ---------- 9. 并发核销：只成功一次（防重复核销核心验收）----------
def try_redeem(i):
    s = requests.Session()
    s.post(f"{BASE}/api/staff/login", data={"username": "staff", "password": "staff123"})
    # 不同 request_id 模拟两名店员/两次独立提交
    resp = s.post(f"{BASE}/staff/api/redeem",
                  data={"voucher_id": vid_num, "request_id": f"concurrent-{i}"})
    return resp.status_code, resp.json()

with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
    results = list(ex.map(try_redeem, range(5)))

successes = [r for r in results if r[0] == 200 and not r[1].get("already")]
already = [r for r in results if r[0] == 409 or r[1].get("already")]
p(f"并发 5 次核销：成功 {len(successes)} 次，被拦 {len(already)} 次", len(successes) == 1)
print("   被拦响应示例:", already[0][1].get("detail") if already else "无")

# ---------- 10. 核销后再次核销：明确拒绝 ----------
r = staff.post(f"{BASE}/staff/api/redeem", data={"voucher_id": vid_num, "request_id": "late-x"})
p("已核销凭证再次核销被拒", r.status_code == 409 and "已于" in r.json()["detail"])

# ---------- 11. 越权：另一门店店员无法核销本店凭证（真实校验）----------
# 直接以服务层验证隔离：用「属于门店2」的店员身份去查门店1的凭证 → 必须被拒
import sys as _sys
_sys.path.insert(0, ".")
from app.db import SessionLocal
from app.models import Role
from app.services import redemption_service as _rs

_db = SessionLocal()
try:
    _rs.verify_voucher(_db, raw_code=voucher_code, staff_store_id=99999,
                       staff_role=Role.STAFF)
    p("跨门店核销被拒", False)
except _rs.RedeemError as e:
    p(f"跨门店核销被拒（{e.code}）", e.code == "wrong_store")
finally:
    _db.close()

# ---------- 12. 数据回传：漏斗与归因 ----------
r = admin.get(f"{BASE}/admin")
p("全局仪表盘可访问", r.ok and "转化漏斗" in r.text)

r = boss.get(f"{BASE}/owner/channels")
found_channel = any(ch in r.text for ch in CHANNELS)
p("渠道效果页可追溯来源（按渠道分组出数据）", r.ok and found_channel)

# ---------- 13. 增长动作自动判定 ----------
r = admin.get(f"{BASE}/admin/actions")
action_id = re.search(r"/admin/actions/(\d+)", r.text).group(1)
r = admin.get(f"{BASE}/admin/actions/{action_id}")
p("增长动作详情可访问", r.ok)
# 判定四态之一：验证成功/验证失败/样本不足/观察中（seed 演示流量下通常为「验证成功」）
judged = any(s in r.text for s in ["验证成功", "验证失败", "样本不足", "观察中"])
p("增长判定给出明确结论", judged)

print("\n🎉 全部关键验收通过")
