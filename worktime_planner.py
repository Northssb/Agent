"""根据一人的实绩和日历排程。标准输入和标准输出均为 JSON。"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time
from fractions import Fraction
from math import ceil
from pathlib import Path
from statistics import median


DAY_MINUTES = 24 * 60
END_OF_CALENDAR = (date.max.toordinal() + 1) * DAY_MINUTES


def read_capacity():
    directory = Path.home() / ".config" / "personal-worktime-agent"
    try:
        with (directory / "availability").open() as stream:
            attributes = os.fstat(stream.fileno())
            if not stat.S_ISREG(attributes.st_mode):
                raise ValueError
            for item in (directory.stat(), attributes):
                if item.st_uid != os.getuid() or item.st_mode & 0o077:
                    raise ValueError
            fraction = Fraction(stream.read().strip())
            if 0 < fraction <= 1:
                return fraction
    except (OSError, ValueError, ZeroDivisionError):
        pass
    raise ValueError("本机排程设置不可用，请检查个人配置后重试。")


def label(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("人员、任务名称和类别须填写非空文本。")
    return value.strip()


def minutes(value):
    if type(value) is not int or value < 1:
        raise ValueError("工时须为正整数分钟。")
    return value


def clock_minutes(value):
    if value == "24:00":
        return DAY_MINUTES
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError("时刻须按 HH:MM 填写。")
    clock = time.fromisoformat(value)
    return clock.hour * 60 + clock.minute


def read_working_hours():
    path = Path.home() / ".config" / "personal-worktime-agent" / "working-hours.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        weekdays, hours = config["weekdays"], config["hours"]
        if not isinstance(weekdays, list) or any(type(day) is not int or not 1 <= day <= 7 for day in weekdays):
            raise ValueError
        if not isinstance(hours, list):
            raise ValueError
        for pair in hours:
            if not isinstance(pair, list) or len(pair) != 2 or clock_minutes(pair[0]) >= clock_minutes(pair[1]):
                raise ValueError
        return {day: hours for day in weekdays}
    except FileNotFoundError:
        return None
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError("个人工作时段配置无效，请检查 working-hours.json。") from error


def effective_hours(day, weekly):
    if weekly is None:
        return day["hours"]
    allowed = weekly.get(date.fromisoformat(day["date"]).isoweekday(), [])
    if "hours" not in day:
        return allowed
    windows = []
    for start, end in day["hours"]:
        if clock_minutes(end) <= clock_minutes(start):
            raise ValueError("同一天的时段终点须晚于起点，跨日时请按日期分段。")
        for opening, closing in allowed:
            left, right = max(start, opening), min(end, closing)
            if left < right:
                windows.append((left, right))
    return windows


def timestamp(value):
    day, clock = value.split("T")
    return date.fromisoformat(day).toordinal() * DAY_MINUTES + clock_minutes(clock)


def without_interval(windows, left, right):
    free = []
    for start, end in windows:
        if end <= left or right <= start:
            free.append((start, end))
            continue
        if start < left:
            free.append((start, left))
        if right < end:
            free.append((right, end))
    return free


class Workday:
    def __init__(self, data, capacity):
        self.date = date.fromisoformat(data["date"])
        self.midnight = self.date.toordinal() * DAY_MINUTES
        self.agenda = []
        self.conflicts = []
        self.free = []
        windows = sorted(self.interval(*pair) for pair in data["hours"])
        for start, end in windows:
            if self.free and start <= self.free[-1][1]:
                left, right = self.free[-1]
                self.free[-1] = (left, max(right, end))
            else:
                self.free.append((start, end))
        working = sum(end - start for start, end in self.free)

        fixed = []
        for event in data.get("fixed", []):
            title = label(event["title"])
            start, end = self.interval(event["start"], event["end"])
            for left, right, other in fixed:
                if start < right and left < end:
                    self.conflicts.append(f"固定日程「{other}」和「{title}」时间重叠。")
            fixed.append((start, end, title))
            self.free = without_interval(self.free, start, end)
            self.add_event(title, start, end, "fixed")
        booked = working - sum(end - start for start, end in self.free)
        self.remaining = int(working * capacity) - booked
        if self.remaining < 0:
            self.conflicts.append("已有固定安排过多，需先调整约定才能继续加排。")

    def interval(self, start, end):
        left = self.midnight + clock_minutes(start)
        right = self.midnight + clock_minutes(end)
        if right <= left:
            raise ValueError("同一天的时段终点须晚于起点，跨日时请按日期分段。")
        return left, right

    def display_time(self, stamp):
        hour, minute = divmod(stamp - self.midnight, 60)
        return f"{hour:02}:{minute:02}"

    def add_event(self, title, start, end, kind):
        self.agenda.append(dict(title=title, start=self.display_time(start),
                                end=self.display_time(end), kind=kind))

    def place(self, task, needed, earliest):
        finish = None
        initial_windows = self.free
        for start, end in initial_windows:
            if self.remaining <= 0 or needed == 0:
                break
            left = max(start, earliest)
            right = min(end, task.deadline)
            take = min(needed, self.remaining, right - left)
            if take <= 0 or (not task.splittable and take < needed):
                continue
            finish = left + take
            self.add_event(task.title, left, finish, "task")
            self.free = without_interval(self.free, left, finish)
            self.remaining -= take
            needed -= take
        return needed, finish

    def result(self):
        return dict(date=self.date.isoformat(),
                    agenda=sorted(self.agenda, key=lambda event: event["start"]),
                    conflicts=self.conflicts)


def analyze(history, person):
    samples = defaultdict(list)
    for record in history:
        if label(record["person"]) == person:
            samples[label(record["category"])].append(minutes(record["minutes"]))
    return {
        category: dict(category=category, samples=len(values), total_minutes=sum(values),
                       median_minutes=median(values))
        for category, values in sorted(samples.items())
    }


@dataclass
class Task:
    title: str
    minutes: int | None
    basis: str
    earliest: int
    deadline: int
    priority: int
    splittable: bool
    after: list
    waiting_for: str


def read_task(raw, analysis):
    title = label(raw["title"])
    sample = analysis.get(label(raw["category"]))
    if raw.get("minutes") is not None:
        estimate, basis = minutes(raw["minutes"]), "本次任务估时"
    elif sample:
        estimate = ceil(sample["median_minutes"])
        basis = f"本人同类实绩中位数（{sample['samples']} 条）"
    else:
        estimate, basis = None, "待补估时"
    priority = raw.get("priority", 2)
    if type(priority) is not int or priority not in (1, 2, 3):
        raise ValueError("任务优先级须为 1、2 或 3。")
    split = raw.get("splittable", False)
    after = raw.get("after", [])
    if not isinstance(after, list) or not isinstance(split, bool):
        raise ValueError("after 须为任务名称数组，splittable 须为布尔值。")
    return Task(title, estimate, basis,
                timestamp(raw["earliest"]) if raw.get("earliest") else 0,
                timestamp(raw["deadline"]) if raw.get("deadline") else END_OF_CALENDAR,
                priority, split, [label(name) for name in after], raw.get("waiting_for", ""))


def planning_order(tasks):
    ordered = {}
    visiting = set()

    def visit(title):
        if title in ordered:
            return
        if title in visiting or title not in tasks:
            raise ValueError(f"请检查前置任务「{title}」是否缺失或形成循环。")
        visiting.add(title)
        for dependency in tasks[title].after:
            visit(dependency)
        visiting.remove(title)
        ordered[title] = tasks[title]

    for task in sorted(tasks.values(), key=lambda item: (item.deadline, item.priority)):
        visit(task.title)
    return ordered.values()


def plan(data):
    person = label(data["person"])
    analysis = analyze(data.get("history", []), person)
    raw_days = data.get("days", [])
    days = []
    if raw_days:
        capacity = read_capacity()
        weekly = read_working_hours()
        raw_days = [{**day, "hours": effective_hours(day, weekly)} for day in raw_days]
        days = sorted((Workday(day, capacity) for day in raw_days), key=lambda day: day.date)
        if len({day.date for day in days}) != len(days):
            raise ValueError("同一日期请合并成一个工作日。")
    as_of = timestamp(data["as_of"]) if data.get("as_of") else 0
    tasks = {}
    for raw in data.get("tasks", []):
        task = read_task(raw, analysis)
        if task.title in tasks:
            raise ValueError(f"任务名称「{task.title}」重复。")
        tasks[task.title] = task

    completed = {}
    estimates = []
    backlog = []
    for task in planning_order(tasks):
        needed = task.minutes
        estimates.append(dict(title=task.title, minutes=needed, basis=task.basis))
        unmet = [name for name in task.after if name not in completed]
        if task.waiting_for:
            reason = f"等待{task.waiting_for}"
        elif unmet:
            reason = "等待前置任务「" + "、".join(unmet) + "」"
        elif needed is None:
            reason = "缺少同类实绩或本次估时"
        else:
            earliest = max([as_of, task.earliest] + [completed[name] for name in task.after])
            for day in days:
                needed, finish = day.place(task, needed, earliest)
                if needed == 0:
                    completed[task.title] = finish
                    break
            if needed == 0:
                continue
            reason = "截止前可用时段不足" if task.deadline < END_OF_CALENDAR else "需要后续可用时段"
        backlog.append(dict(title=task.title, remaining_minutes=needed, reason=reason))
    return dict(person=person, analysis=list(analysis.values()), estimates=estimates,
                days=[day.result() for day in days], backlog=backlog)


if __name__ == "__main__":
    try:
        result = plan(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        sys.exit(f"排程未生成。{error}")
    print(json.dumps(result, ensure_ascii=False))
