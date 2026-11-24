# 估时输入与结果

在 Skill 目录内运行 `python3 scripts/estimate.py`，通过标准输入传入一个 JSON 对象。以下为演示数据。

```json
{
  "person": "林青",
  "history": [
    {"person":"林青","activity":"托书复核","unit":"票","case":"示例A","minutes":30},
    {"person":"林青","activity":"托书复核","unit":"票","case":"示例B","minutes":45},
    {"person":"林青","activity":"托书复核","unit":"票","case":"示例C","minutes":60}
  ],
  "steps": [
    {"activity":"整理本批资料","unit":"批","per_unit_minutes":10},
    {"activity":"托书复核","unit":"票","quantity":2}
  ],
  "waits": ["等待承运人确认，回传时间未知"]
}
```

每个 history 样本代表本人完成一份可比交付的主动分钟。`case` 在同一人、活动和单位内标识该交付。不同人员或不同单位会分开计算。类别应反映作业差异，如普通票和改单票。不同计时片段由调用方按交付先合并。同一次交付被重复导出可按样本标识去重。

`steps` 中的 quantity 默认 1。per_unit_minutes 表示本次已确认的单份估时。存在可比样本时，取中位数乘以数量，最后向上取整到分钟。至少两份样本时同时给出历史最小、最大值按数量换算的范围。只有一份时标明样本少。无样本可按有依据的假设传 `"assumption":{"low":10,"likely":20,"high":45,"basis":"说明单份估值如何得到"}`，三者均按单份主动分钟计算。没有依据就省略估值。

上述示例得到 100 人分钟，批次准备 10 分钟、两票复核合计 90 分钟。等待单独列示。每步保留依据和样本数。总量中若有未知项，total_minutes 为空，known_minutes 给出已知小计。若至少一步有范围，其余步骤均有范围或有本次明确给定的点估，total_range_minutes 汇总低、高两个情景。给定点估在两个情景中保持相同。示例中批次准备固定为 10 分钟，总范围为 70–130 分钟。存在待估步骤或仅有单一样本的步骤时，总范围留空。这是情景范围，不提供统计置信水平。

接入本项目排程器时，将单步的总 minutes 传入 tasks[].minutes，已经包含 quantity。对上游依赖使用 after，对尚待外部回传的后续工作使用 waiting_for。只做估时时直接返回估时结果。
