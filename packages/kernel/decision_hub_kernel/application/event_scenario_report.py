"""Readable projection of already checked event-plan originals; no new forecast."""

from decimal import Decimal

from ..ports.event_scenario_manifest import unpack_event_scenario_manifest
from .provider_input_receipts import strict_json
from .research_publication_binding import observation_json


def _percent(value):
    return format(Decimal(value) * 100, "f") + "%"


def _distribution(rows):
    labels = {"up": "上涨", "neutral": "区间内", "down": "下跌"}
    return "、".join(
        labels.get(row["outcome_id"], row["outcome_id"]) + _percent(row["value"])
        for row in rows
    ) if rows is not None else "概率未知"


def _predicate(value):
    metrics = {
        "headline_mom": "总体CPI环比", "core_mom": "核心CPI环比",
        "headline_yoy": "总体CPI同比", "core_yoy": "核心CPI同比",
        "payroll_change": "新增非农就业", "unemployment_rate": "失业率",
        "target_rate": "政策利率", "headline_pce_mom": "总体PCE环比",
        "core_pce_mom": "核心PCE环比",
    }
    operators = {"lt": "低于", "le": "不高于", "eq": "等于",
                 "ne": "不等于", "ge": "不低于", "gt": "高于"}
    units = {"percent": "%", "pp": "个百分点", "percentage_point": "个百分点"}
    return "；或：".join(
        "且".join(
            metrics.get(term["metric_id"], term["metric_id"])
            + operators[term["operator"]] + term["value"]
            + units.get(term["unit"], " " + term["unit"])
            for term in group["all_of"]
        )
        for group in value["any_of"]
    )


def event_scenario_report_projection(synthesis, catalog):
    """Retain all branches and exact p/q/economics without inventing reasons."""
    if synthesis["schema_version"] != "research-decision-synthesis.v3":
        return [], [], []
    row = next(item for item in catalog["originals"] if item["role"] == "event_scenario_plan")
    material = unpack_event_scenario_manifest(strict_json(row["body_json"])["manifest"])
    plan = material["event_plan"]
    event = plan["event"]
    family = {"cpi": "CPI通胀数据", "pce": "PCE通胀数据", "nfp": "美国就业数据",
              "fomc": "美联储议息"}.get(event["family"], event["family"])
    probabilities = {item["outcome_id"]: item["value"]
                     for item in plan["outcome_given_occurrence"]["probabilities"]}
    predicates = {item["scenario_id"]: item for item in plan["scenario_predicates"]}
    originals = {observation_json(item["source_ref"]): strict_json(item["body_json"])
                 for item in material["declared_originals"]}
    actions = {"open_long": "做多", "open_short": "做空", "no_change": "维持当前仓位",
               "reduce_half": "减仓一半", "close": "平仓"}
    positions = {"flat": "空仓", "long": "持有多头仓位", "short": "持有空头仓位"}
    cited = ["事件情景原件 — " + synthesis["event_plan_ref"]["ref"]]
    marginal = plan["marginal_distribution"]
    headline = (
        f"事前事件情景：{family}，计划发布时间{event['scheduled_at']}；"
        f"已列出{len(plan['branches'])}个条件预案。"
    )
    summary = [headline]
    if marginal is not None:
        summary.append("按事件情景概率加权的市场方向：" + _distribution(marginal["probabilities"]))
    most_likely = max(probabilities, key=lambda key: Decimal(probabilities[key]))
    details = [headline + "概率为事前研究估计；单位收益依赖列明的成交及成本假设。"]
    for index, branch in enumerate(plan["branches"], 1):
        predicate = _predicate(predicates[branch["scenario_id"]]["predicate"])
        probability = _percent(probabilities[branch["scenario_id"]])
        direction = _distribution(
            branch["market_distribution"]["direction_distribution"]["probabilities"]
        )
        choices = []
        for position, bundle in branch["comparisons_by_position"].items():
            comparison = bundle["comparison"]
            selected = comparison["selected_action_id"]
            choices.append(
                f"{positions[position]}首选预案：{actions.get(selected, selected)}；"
                f"每{comparison['unit_notional_usdt']} USDT研究单位、按假设成交的含成本净收益："
                + "、".join(
                    actions.get(item["candidate_id"], item["candidate_id"]) + " "
                    + str(item["unit_expected_net_if_assumed_fills_usdt"] or "未知") + " USDT"
                    for item in comparison["candidate_results"]
                )
            )
        reasons = []
        for reference in branch["condition_assumption_refs"]:
            original = originals.get(observation_json(reference), {})
            description = next(
                (original[key] for key in
                 ("statement", "mechanism", "explanation", "reason", "thesis")
                 if isinstance(original.get(key), str) and original[key].strip()), None
            )
            if description:
                reasons.append(description)
            cited.append("情景根因依据 — " + reference["ref"])
        reason = "；".join(reasons) or "未提供分支专属原因说明，主假设、反证及来源见主分析。"
        domain = branch["applicability_domain"]
        details.append(
            f"情景{index}：{predicate}\n情景发生概率（事件发生条件下）：{probability}；"
            f"条件方向概率：{direction}\n根因依据：{reason}\n"
            + "\n".join(choices)
            + f"\n确认与期限：生效{branch['applicable_from']}，最迟{branch['valid_until']}；"
            + f"目标窗口{branch['target']['window_start']}至{branch['target']['window_end']}。"
            + f"执行前报价年龄不得超过{domain['maximum_quote_age_seconds']}秒，价格相对基准须在"
            + f"{domain['price_ratio_min']}至{domain['price_ratio_max']}倍内；"
            + f"目标窗口开始后最多{domain['maximum_elapsed_seconds']}秒内确认。"
            + "超出范围、原假设失效或账户变化须重新判断；当前方案不代表已成交。"
            + ("\n已列明失效依据，完整内容见原件。" if domain["invalidation_refs"] else "")
            + ("\n本情景仍有待补充输入，详见原件与主分析的未决问题。"
               if branch["unknown_reasons"] else "")
        )
        if branch["scenario_id"] == most_likely:
            selected = branch["comparisons_by_position"]["flat"]["comparison"]["selected_action_id"]
            summary.append(
                f"最高概率事件情景之一：{predicate}（{probability}）；"
                f"空仓首选预案：{actions.get(selected, selected)}，须满足对应确认条件。"
            )
    return summary, details, list(dict.fromkeys(cited))
