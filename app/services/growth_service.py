"""增长动作效果判定服务。

根据观察周期、成功指标、成功阈值、最低样本量与实际漏斗数据自动判定：
  - 独立扫码 < 最低样本量           → 样本不足（暂时无法判断，不能因数据少就判失败）
  - 样本达标 且 指标值 ≥ 阈值        → 验证成功
  - 样本达标 且 观察期结束 且 未达阈值 → 验证失败
  - 其余（样本达标、观察中、未达阈值）→ 暂时无法判断（观察中）
"""
from ..models import ActionResult, GrowthAction, now_utc
from .stats_service import funnel


def evaluate(db, action: GrowthAction) -> dict:
    f = funnel(db, {"store_id": action.store_id, "growth_action_id": action.id})
    unique_scan = f["unique_scan"]

    metric_value = {
        "scan": unique_scan,
        "reserve": f["reserve"],
        "redeem": f["redeem"],
    }.get(action.success_metric, f["redeem"])

    observe_ended = bool(action.ends_at and now_utc() > action.ends_at)

    if unique_scan < (action.min_sample or 0):
        result = ActionResult.INSUFFICIENT
        conclusion = (
            f"独立扫码 {unique_scan} 人，未达最低样本量 {action.min_sample}，"
            f"暂时无法判断，需继续观察。"
        )
        suggestion = "加大投放或延长观察周期，积累足够样本后再做结论。"
    elif metric_value >= (action.success_threshold or 0):
        result = ActionResult.SUCCESS
        conclusion = (
            f"样本充足（{unique_scan}≥{action.min_sample}），"
            f"{_metric_label(action.success_metric)} {metric_value} 达到阈值 {action.success_threshold}，"
            f"假设验证成功。"
        )
        suggestion = "该策略有效，可扩大投放规模或复制到相似场景。"
    elif observe_ended:
        result = ActionResult.FAILED
        conclusion = (
            f"样本充足但观察期结束时，{_metric_label(action.success_metric)} {metric_value} "
            f"未达阈值 {action.success_threshold}，假设验证失败。"
        )
        suggestion = "调整核心假设（套餐卖点/价格/渠道/内容）后重新验证。"
    else:
        result = ActionResult.UNDECIDED
        conclusion = (
            f"样本充足，但 {_metric_label(action.success_metric)} {metric_value} "
            f"尚未达到阈值 {action.success_threshold}，观察期未结束，继续观察。"
        )
        suggestion = "保持投放，观察期结束后再判定。"

    return {
        "result": result,
        "metric": action.success_metric,
        "metric_value": metric_value,
        "threshold": action.success_threshold,
        "min_sample": action.min_sample,
        "unique_scan": unique_scan,
        "observe_ended": observe_ended,
        "funnel": f,
        "conclusion": conclusion,
        "suggestion": suggestion,
    }


def _metric_label(metric: str) -> str:
    return {"scan": "独立扫码人数", "reserve": "预约人数", "redeem": "核销人数"}.get(metric, "核销人数")


def refresh_result(db, action: GrowthAction) -> str:
    """把判定结果写回 action.result 字段（执行状态 status 不受影响）。"""
    ev = evaluate(db, action)
    action.result = ev["result"]
    db.commit()
    return ev["result"]
