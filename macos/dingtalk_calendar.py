#!/usr/bin/python3 -I
"""通过 stdio 为个人工时助手提供只读的本人钉钉忙闲查询。"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


TOOL = {
    "name": "my_busy",
    "description": "只读查询已登录用户本人钉钉日历的忙碌时段。空列表表示查询成功且没有占用。",
    "inputSchema": {
        "type": "object",
        "properties": {name: {"type": "string", "format": "date-time"} for name in ("start", "end")},
        "required": ["start", "end"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False},
}


def instant(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.utcoffset() is None:
        raise ValueError
    return moment


def query_busy(args):
    try:
        if not isinstance(args, dict) or set(args) != {"start", "end"}:
            raise ValueError
        start, end = instant(args["start"]), instant(args["end"])
        if not timedelta(0) < end - start <= timedelta(days=31):
            raise ValueError
    except (ValueError, OverflowError):
        raise ValueError("请提供带时区的 ISO8601 起止时间，每次查询不超过 31 天。") from None

    candidates = (Path.home() / ".local/bin/dws", Path("/opt/homebrew/bin/dws"), Path("/usr/local/bin/dws"))
    dws = next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
    if dws is None:
        raise ValueError("未找到已安装的钉钉官方 dws，本次忙闲未核实。")
    try:
        response = subprocess.run(
            [str(dws), "calendar", "+my-free", "--start", start.isoformat(), "--end", end.isoformat(),
             "--format", "json"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=Path.home(), timeout=45, check=True,
        )
        result = json.loads(response.stdout)
        if result["ok"] is not True or result.get("outcome", "success") != "success":
            raise ValueError
        data = result["data"]
        periods = data["busy"]
        if not isinstance(periods, list):
            raise ValueError
        busy = []
        for period in periods:
            left, right = instant(period["start"]), instant(period["end"])
            if right <= left:
                raise ValueError
            left, right = max(start, left), min(end, right)
            if left < right:
                busy.append({"start": left.isoformat(), "end": right.isoformat()})
        if "free" in data and data["free"] is not (not busy):
            raise ValueError
        return {"busy": busy}
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError, OverflowError):
        raise ValueError("钉钉忙闲未核实，请检查本机 dws 授权状态和网络后重试。") from None


def dispatch(request):
    method = request.get("method")
    params = request.get("params", {})
    if not isinstance(params, dict):
        return {"error": {"code": -32602, "message": "参数无效"}}
    if method == "initialize":
        supported = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
        requested = params.get("protocolVersion")
        return {"result": {"protocolVersion": requested if requested in supported else supported[-1],
                           "capabilities": {"tools": {}}, "serverInfo": {"name": "worktime_calendar", "version": "1.0"}}}
    if method == "ping":
        return {"result": {}}
    if method == "tools/list":
        return {"result": {"tools": [TOOL]}}
    if method == "tools/call":
        if params.get("name") != TOOL["name"]:
            return {"error": {"code": -32602, "message": "未知工具"}}
        try:
            answer = json.dumps(query_busy(params.get("arguments")), ensure_ascii=False)
            return {"result": {"content": [{"type": "text", "text": answer}]}}
        except ValueError as error:
            return {"result": {"isError": True, "content": [{"type": "text", "text": str(error)}]}}
    return {"error": {"code": -32601, "message": "不支持该方法"}}


if __name__ == "__main__":
    for line in sys.stdin.buffer:
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                reply = {"error": {"code": -32600, "message": "请求无效"}}
            elif "id" not in request:
                continue
            else:
                request_id = request["id"]
                reply = dispatch(request)
        except ValueError:
            reply = {"error": {"code": -32700, "message": "请求不是有效 JSON"}}
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, **reply}, ensure_ascii=False), flush=True)
