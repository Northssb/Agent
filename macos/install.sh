#!/bin/bash
set -eu

project_dir="$(cd "$(dirname "$0")/.." && pwd -P)"
apps_dir="$HOME/Applications"
app="$apps_dir/工时助手.app"
shortcut="$HOME/Desktop/工时助手.app"
bundle_id="local.worktime-planner.chat"
codex_path="$(command -v codex || true)"
if [[ -z "$codex_path" ]]; then
    codex_path="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
fi
if [[ ! -x "$codex_path" ]]; then
    echo "请先安装并登录 Codex，再运行安装脚本。" >&2
    exit 1
fi

if [[ -e "$app" || -L "$app" ]]; then
    if [[ -L "$app" || "$( /usr/bin/plutil -extract CFBundleIdentifier raw "$app/Contents/Info.plist" 2>/dev/null || true )" != "$bundle_id" ]]; then
        echo "应用程序目录已有同名项目，请先核对：$app" >&2
        exit 1
    fi
fi
if [[ -e "$shortcut" || -L "$shortcut" ]]; then
    if [[ ! -L "$shortcut" || "$(readlink "$shortcut")" != "$app" ]]; then
        echo "桌面已有同名项目，请先核对：$shortcut" >&2
        exit 1
    fi
fi

mkdir -p "$apps_dir"
stage="$(mktemp -d "$apps_dir/.worktime-chat.XXXXXXXX")"
trap 'rm -rf "$stage"' EXIT
contents="$stage/工时助手.app/Contents"
mkdir -p "$contents/MacOS" "$contents/Resources"
# 部分升级过的 Command Line Tools 同时安装了两个 SwiftBridging 定义。
# 仅在这次编译中隐藏旧定义，保留系统工具链原样。
swift_includes="$(dirname "$(/usr/bin/xcrun --find swiftc)")/../include/swift"
set --
if [[ -f "$swift_includes/module.modulemap" && -f "$swift_includes/bridging.modulemap" ]] &&
    /usr/bin/grep -q '^module SwiftBridging ' "$swift_includes/module.modulemap" &&
    /usr/bin/grep -q '^module SwiftBridging ' "$swift_includes/bridging.modulemap"; then
    python3 - "$stage" "$swift_includes" <<'PY'
import json
import sys
from pathlib import Path

stage, headers = map(Path, sys.argv[1:])
empty = stage / "empty.modulemap"
empty.touch()
entry = {"type": "file", "name": str((headers / "module.modulemap").resolve()),
         "external-contents": str(empty)}
(stage / "overlay.json").write_text(json.dumps({"version": 0, "roots": [entry]}))
PY
    set -- -vfsoverlay "$stage/overlay.json"
fi
/usr/bin/xcrun swiftc "$@" -target "$(/usr/bin/uname -m)-apple-macosx13.0" \
    -swift-version 5 -parse-as-library -O \
    "$project_dir/macos/ChatWindow.swift" "$project_dir/macos/ChatSession.swift" \
    -o "$contents/MacOS/WorktimeChat"
cp "$project_dir/macos/Worktime.icns" "$contents/Resources/"
install -m 755 "$project_dir/macos/dingtalk_calendar.py" "$contents/Resources/dingtalk_calendar.py"
agent="$contents/Resources/Agent"
mkdir -p "$agent/.codex/agents" "$agent/.agents/skills"
cp "$project_dir"/{AGENTS.md,personal_plan.py,plan_resume.py,plan_tasks.py,worktime_planner.py} "$agent/"
cp "$project_dir/.codex/agents/worktime_planner.toml" "$agent/.codex/agents/"
cp -R "$project_dir/.agents/skills/estimate-task-effort" "$agent/.agents/skills/"
if [[ -d "$project_dir/.local-data/freight-industry-library" ]]; then
    mkdir -p "$agent/.local-data"
    cp -R "$project_dir/.local-data/freight-industry-library" "$agent/.local-data/"
fi
python3 - "$contents/Info.plist" "$codex_path" "$bundle_id" <<'PY'
import plistlib
import sys
from pathlib import Path

target, codex, identifier = sys.argv[1:]
info = dict(CFBundleName="工时助手", CFBundleDisplayName="工时助手",
            CFBundleIdentifier=identifier, CFBundlePackageType="APPL",
            CFBundleExecutable="WorktimeChat", CFBundleVersion="1",
            CFBundleShortVersionString="1.0", CFBundleIconFile="Worktime.icns",
            LSApplicationCategoryType="public.app-category.productivity",
            LSMinimumSystemVersion="13.0", LSMultipleInstancesProhibited=True,
            NSHighResolutionCapable=True,
            WorktimeCodexPath=str(Path(codex).absolute()))
Path(target).write_bytes(plistlib.dumps(info))
PY
/usr/bin/codesign --force --sign - "$stage/工时助手.app"
/usr/bin/codesign --verify --strict "$stage/工时助手.app"

if [[ -d "$app" ]]; then mv "$app" "$stage/previous.app"; fi
if ! mv "$stage/工时助手.app" "$app"; then
    if [[ -d "$stage/previous.app" ]]; then mv "$stage/previous.app" "$app"; fi
    exit 1
fi
if [[ ! -L "$shortcut" ]]; then ln -s "$app" "$shortcut"; fi
# 旧版估时技能曾以符号链接安装在文稿目录。仅迁移本工程创建的链接。
python3 - "$project_dir" "$app" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

project, app = map(Path, sys.argv[1:])
skill = Path(".agents/skills/estimate-task-effort")
link = Path.home() / ".codex/skills/estimate-task-effort"
if link.is_symlink() and link.resolve() == (project / skill).resolve():
    with tempfile.TemporaryDirectory(dir=link.parent) as tmp:
        replacement = Path(tmp) / link.name
        replacement.symlink_to(app / "Contents/Resources/Agent" / skill)
        os.replace(replacement, link)
PY
echo "已安装：$app"
echo "桌面入口：$shortcut"
