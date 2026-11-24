"""以 JSON 读取单人工作分解，估算主动投入的人分钟。"""

import json
import sys
from collections import defaultdict
from math import ceil
from statistics import median


def label(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("请填写有效的人员、活动、单位、样本标识或估时依据。")
    return value.strip()


def positive(value):
    if type(value) is not int or value < 1:
        raise ValueError("数量和单份工时须为正整数。")
    return value


def category(row):
    return label(row["activity"]), label(row["unit"])


def personal_history(records, person):
    groups = defaultdict(dict)
    for row in records:
        if label(row["person"]) != person:
            continue
        cases = groups[category(row)]
        case, value = label(row["case"]), positive(row["minutes"])
        if case in cases and cases[case] != value:
            raise ValueError(f"样本「{case}」工时冲突，请核对原始记录。")
        cases[case] = value
    return groups


def estimate_step(row, history):
    activity, unit = category(row)
    quantity = positive(row.get("quantity", 1))
    observed = list(history.get((activity, unit), {}).values())
    point = None
    bounds = None
    if row.get("per_unit_minutes") is not None:
        point = positive(row["per_unit_minutes"])
        basis = "本次确认的单份估时"
    elif observed:
        point = median(observed)
        basis = "本人同类交付的中位数"
        if len(observed) > 1:
            bounds = [min(observed), max(observed)]
    elif row.get("assumption") is not None:
        assumption = row["assumption"]
        low, point, high = (positive(assumption[k]) for k in ("low", "likely", "high"))
        if not low <= point <= high:
            raise ValueError("三点初估须满足低值 ≤ 最可能值 ≤ 高值。")
        bounds = [low, high]
        basis = "情景初估，" + label(assumption["basis"])
    else:
        basis = "待补本人实绩或单项估时"
    return {
        "activity": activity, "unit": unit, "quantity": quantity,
        "samples": len(observed), "basis": basis,
        "minutes": ceil(quantity * point) if point is not None else None,
        "range_minutes": [quantity * x for x in bounds] if bounds else None,
    }


def estimate(data):
    person = label(data["person"])
    history = personal_history(data.get("history", []), person)
    steps = [estimate_step(row, history) for row in data["steps"]]
    waits = data.get("waits", [])
    if not isinstance(waits, list):
        raise ValueError("外部等待请按事项传入数组。")
    waits = [label(wait) for wait in waits]
    if not steps and not waits:
        raise ValueError("请提供要估算的工作步骤或等待事项。")
    known = sum(step["minutes"] for step in steps if step["minutes"] is not None)
    complete = all(step["minutes"] is not None for step in steps)
    total_range = None
    if any(step["range_minutes"] for step in steps):
        ranges = []
        for raw, step in zip(data["steps"], steps):
            if step["range_minutes"] is not None:
                ranges.append(step["range_minutes"])
            elif raw.get("per_unit_minutes") is not None:
                ranges.append([step["minutes"], step["minutes"]])
            else:
                break
        else:
            total_range = [sum(values) for values in zip(*ranges)]
    return {
        "person": person, "steps": steps, "waits": waits, "known_minutes": known,
        "total_minutes": known if complete else None, "total_range_minutes": total_range,
    }


if __name__ == "__main__":
    try:
        result = estimate(json.load(sys.stdin))
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        sys.exit(f"估时输入需核对。{error}")
    print(json.dumps(result, ensure_ascii=False))
