"""个人计划的任务标识、前置关系和进度反馈。"""

from uuid import uuid4

from worktime_planner import label, planning_order, read_task, timestamp


SCHEDULING_FIELDS = ("category", "after", "priority", "earliest", "deadline",
                     "splittable", "waiting_for")


def upsert_tasks(tasks, changes, analysis):
    by_id = {t["id"]: t for t in tasks}
    by_name = {(t["project"], t["title"]): t for t in tasks}
    for change in changes:
        if "id" in change:
            task = by_id.get(label(change["id"]))
            if task is None:
                raise ValueError("指定的任务标识已不存在，请重新查看计划。")
        else:
            project = change.get("project", "")
            if not isinstance(project, str):
                raise ValueError("项目名称须为文本。")
            task = by_name.get((project.strip(), label(change["title"])))
        if task is None:
            task = dict(id=str(uuid4()), project="", title="", original_minutes=None,
                        remaining_minutes=None, worked_minutes=0, status="active", after=[])
            tasks.append(task)
            by_id[task["id"]] = task
        else:
            del by_name[(task["project"], task["title"])]

        project = change.get("project", task["project"])
        if not isinstance(project, str):
            raise ValueError("项目名称须为文本。")
        key = (project.strip(), label(change.get("title", task["title"])))
        if key in by_name:
            raise ValueError("同一项目已存在同名任务，请核对标识。")
        task["project"], task["title"] = key
        by_name[key] = task
        task.update((k, change[k]) for k in SCHEDULING_FIELDS if k in change)
        if "estimate_note" in change:
            task["estimate_note"] = label(change["estimate_note"])
        estimate = read_task({**task, "minutes": change.get("minutes")}, analysis)
        if task["original_minutes"] is None and estimate.minutes is not None:
            task["original_minutes"] = estimate.minutes
            task["original_basis"] = estimate.basis
            if task["remaining_minutes"] is None and task["status"] == "active":
                task["remaining_minutes"] = estimate.minutes

    # 新任务可以依赖同批次中尚未出现的任务，待标识齐全后再解析。
    graph = {}
    for task in tasks:
        deps = []
        for ref in task["after"]:
            ref = label(ref)
            dep = by_id.get(ref) or by_name.get((task["project"], ref))
            if dep is None:
                raise ValueError(f"找不到前置任务「{ref}」，跨项目请使用任务标识。")
            if dep["id"] not in deps:
                deps.append(dep["id"])
        task["after"] = deps
        graph[task["id"]] = read_task({**task, "title": task["id"]}, {})
    planning_order(graph)
    return by_id


def apply_feedback(task, report, as_of):
    for key in ("worked_minutes", "remaining_minutes"):
        if key in report and (type(report[key]) is not int or report[key] < 0):
            raise ValueError(f"{key} 须为非负整数分钟。")
    if "done" in report and type(report["done"]) is not bool:
        raise ValueError("done 须为布尔值。")
    if report.get("done") and report.get("remaining_minutes", 0) != 0:
        raise ValueError("完成状态与剩余工时冲突，请核对进度。")
    worked = report.get("worked_minutes", task["worked_minutes"])
    if report.get("done"):
        remaining, status = 0, "done"
    elif "remaining_minutes" in report:
        remaining = report["remaining_minutes"]
        status = "active" if remaining else "done"
        # 保存本人最近一次对剩余工作的判断，后续只扣新增的累计投入。
        task["revised_total_minutes"] = worked + remaining
    elif "worked_minutes" in report:
        if task["status"] == "done":
            remaining, status = 0, "done"
        else:
            estimate = task.get("revised_total_minutes", task["original_minutes"])
            remaining = estimate - worked if estimate is not None and estimate > worked else None
            # 累计投入用完预计工时仍需本人确认是否完成。
            status = "active" if remaining is not None else "pending"
    else:
        raise ValueError("请提供已完成、累计已做分钟或剩余分钟。")
    task.update(worked_minutes=worked, remaining_minutes=remaining, status=status,
                progress_as_of=as_of)


def apply_updates(tasks, new_tasks, feedback, previous_days, as_of, analysis):
    """在内存快照上合并任务和反馈，错误由外层事务回滚。"""
    by_id = upsert_tasks(tasks, new_tasks, analysis)
    now = timestamp(as_of)
    for day in previous_days:
        for event in day["agenda"]:
            task = by_id.get(event.get("task_id"))
            if task is None or task["status"] != "active":
                continue
            start = timestamp(day["date"] + "T" + event["start"])
            end = timestamp(day["date"] + "T" + event["end"])
            confirmed = timestamp(task["progress_as_of"]) if task.get("progress_as_of") else 0
            if max(start, confirmed) < min(now, end):
                task["status"] = "pending"
    for report in feedback:
        task = by_id.get(label(report["id"]))
        if task is None:
            raise ValueError("进度反馈中的任务不存在，请重新查看计划。")
        apply_feedback(task, report, as_of)
