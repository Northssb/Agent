"""从最近一次个人计划恢复任务，把本次核实的工作日交给排程器。"""

from copy import deepcopy
from datetime import date as local_date

from plan_tasks import apply_updates
from worktime_planner import analyze, clock_minutes, label, plan, timestamp


def fixed_events(events):
    if not isinstance(events, list):
        raise ValueError("固定事项须为包含名称及起止时间的数组。")
    unique = {}
    for event in events:
        title, start, end = label(event["title"]), event["start"], event["end"]
        if clock_minutes(end) <= clock_minutes(start):
            raise ValueError("固定事项须在当天结束，跨日请先按日期拆分。")
        unique[title, start, end] = dict(title=title, start=start, end=end)
    return list(unique.values())


def confirmed_days(requested, manual_by_date):
    if not isinstance(requested, list) or not requested:
        raise ValueError("请提供要安排的工作日及其日历核对状态。")
    unknown = [day["date"] for day in requested
               if day.get("calendar_status") not in ("verified", "manual")]
    if unknown:
        raise ValueError("下列日期的会议占用尚未核实：" + "、".join(unknown))

    days = []
    for day in requested:
        date = day["date"]
        if "manual_fixed" in day:
            manual_by_date[date] = fixed_events(day["manual_fixed"])
        # 钉钉占用取自本次查询，手工约定和实际工作从本机计划恢复。
        fixed = fixed_events(day.get("fixed", []) + manual_by_date.get(date, []))
        days.append(dict(date=date, hours=day["hours"], fixed=fixed))
    return days


def scheduler_tasks(tasks):
    by_id = {task["id"]: task for task in tasks}
    rows = []
    for task in tasks:
        if task["status"] == "done":
            continue
        row = {key: task[key] for key in ("category", "deadline", "earliest", "priority",
                                           "splittable", "waiting_for") if key in task}
        row.update(title=task["id"], minutes=task["remaining_minutes"],
                   after=[key for key in task["after"] if by_id[key]["status"] != "done"])
        if task["status"] == "pending":
            row["waiting_for"] = "本人确认进度"
        rows.append(row)
    return rows


def restore_titles(result, tasks):
    titles = {t["id"]: (f"{t['project']} · {t['title']}" if t["project"] else t["title"])
              for t in tasks}
    by_id = {task["id"]: task for task in tasks}
    for estimate in result["estimates"]:
        task = by_id[estimate["title"]]
        if task.get("progress_as_of"):
            estimate["basis"] = "按本人最新进度核算"
        else:
            estimate["basis"] = task.get("estimate_note") or task.get("original_basis", estimate["basis"])
    rows = result["estimates"] + result["backlog"]
    for day in result["days"]:
        rows.extend(event for event in day["agenda"] if event["kind"] == "task")
    for row in rows:
        key = row["title"]
        row.update(title=titles[key], task_id=key)
        reason = row.get("reason", "")
        if reason.startswith("等待前置任务「") and reason.endswith("」"):
            refs = reason[len("等待前置任务「"):-1].split("、")
            row["reason"] = "等待前置任务「" + "、".join(titles.get(ref, ref) for ref in refs) + "」"


def with_dependents(task_ids, tasks):
    affected = set(task_ids)
    children = {}
    for task in tasks:
        for key in task["after"]:
            children.setdefault(key, []).append(task["id"])
    queue = list(affected)
    while queue:
        for child in children.get(queue.pop(), ()):
            if child not in affected:
                affected.add(child)
                queue.append(child)
    return affected


def keep_other_days(old_days, result, before, tasks):
    """缩小排程范围时保留无关任务的原建议；旧会期并非本次读取。"""
    dates = {day["date"] for day in result["days"]}
    prior = {task["id"]: task for task in before}
    affected = {task["id"] for task in tasks if task != prior.get(task["id"])}
    for day in old_days + result["days"]:
        if day["date"] in dates:
            affected.update(e["task_id"] for e in day["agenda"] if e["kind"] == "task")
    affected = with_dependents(affected, tasks)
    kept, superseded = [], []
    for day in old_days:
        if day["date"] in dates:
            continue
        agenda = [e for e in day["agenda"] if e.get("task_id") not in affected]
        if len(agenda) < len(day["agenda"]) and day["date"] >= result["as_of"][:10]:
            superseded.append(day["date"])
        if any(e["kind"] == "task" for e in agenda):
            kept.append({**day, "agenda": agenda})
    result.update(other_days=kept, superseded_dates=superseded)


def build_snapshot(previous, data):
    person = label(data["person"])
    as_of = data["as_of"]
    manual_by_date = dict(previous.get("manual_by_date", {}))
    days = confirmed_days(data["days"], manual_by_date)
    tasks = deepcopy(previous["tasks"])
    history = data.get("history", [])
    analysis = analyze(history, person)
    old_days = previous["days"] + previous.get("other_days", [])
    apply_updates(tasks, data.get("tasks", []), data.get("feedback", []), old_days,
                  as_of, analysis)
    # 展示的剩余工时已经包含本次反馈，不能再用同类实绩覆盖它。
    result = plan(dict(person=person, days=days, tasks=scheduler_tasks(tasks), as_of=as_of))
    restore_titles(result, tasks)

    result.update(as_of=as_of, tasks=tasks, needs_replan=False, analysis=list(analysis.values()),
                  manual_by_date=manual_by_date)
    keep_other_days(old_days, result, previous["tasks"], tasks)
    return result


def record_feedback(previous, data):
    """只登记本人确认的进度，不用旧会期重新计算日程。"""
    reports = data["feedback"]
    if not isinstance(reports, list) or not reports:
        raise ValueError("请提供要更新的任务进度。")
    snapshot = deepcopy(previous)
    tasks = snapshot["tasks"]
    old_days = snapshot["days"] + snapshot.get("other_days", [])
    apply_updates(tasks, [], reports, old_days, data["as_of"], {})
    manual = snapshot.setdefault("manual_by_date", {})
    for date, events in data.get("manual_by_date", {}).items():
        local_date.fromisoformat(date)
        manual[date] = fixed_events(events)

    # 上游剩余工时变化后，下游旧建议也需要重排。其他任务保留原时段以便下次核对进度。
    before = {t["id"]: (t["status"], t["remaining_minutes"]) for t in previous["tasks"]}
    invalid = {t["id"] for t in tasks if before[t["id"]] != (t["status"], t["remaining_minutes"])}
    reported = {label(report["id"]) for report in reports}
    now = timestamp(data["as_of"])
    for day in old_days:
        date = day["date"]
        manual_changed = manual.get(date, []) != previous.get("manual_by_date", {}).get(date, [])
        for event in day["agenda"]:
            key = event.get("task_id")
            if key and (manual_changed or (key in reported and timestamp(date + "T" + event["start"]) < now)):
                invalid.add(key)
    invalid = with_dependents(invalid, tasks)
    for day in old_days:
        day["agenda"] = [event for event in day["agenda"] if event.get("task_id") not in invalid]
    for field in ("backlog", "estimates"):
        snapshot[field] = [entry for entry in snapshot.get(field, []) if entry["task_id"] not in invalid]
    needs_replan = snapshot.get("needs_replan", False) or bool(invalid) or manual != previous.get("manual_by_date", {})
    snapshot.update(as_of=data["as_of"], needs_replan=needs_replan, superseded_dates=[])
    return snapshot
