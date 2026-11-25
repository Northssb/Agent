# 个人工时排程

当用户想分析自己的工时、安排工作、反馈任务进度或说「继续排」时，读取 `.codex/agents/worktime_planner.toml` 中的 `developer_instructions`，在当前对话按其中的流程执行。跨对话续排先恢复本机个人总计划。

只问一项或一批任务需要多少人时，或要求评估 M4 需求时，按 [任务工时估算](.agents/skills/estimate-task-effort/SKILL.md) 执行。

用户只想查货代行业资料时，按[中国海运出口货代业务资料](.agents/skills/estimate-task-effort/references/freight-operations.md)给出匹配的原文、章节和适用场景。
