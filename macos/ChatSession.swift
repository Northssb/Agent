import AppKit
import Combine
import Foundation

struct ChatMessage: Identifiable, Codable {
    let id: UUID
    let role: String
    let text: String

    init(role: String, text: String) {
        self.id = UUID()
        self.role = role
        self.text = text
    }
}

private struct Transcript: Codable {
    var threadID: UUID?
    var messages: [ChatMessage]
}

@MainActor
final class ChatSession: ObservableObject {
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var busy = false
    @Published private(set) var errorMessage: String?

    private let projectURL: URL?
    private let codexURL: URL?
    private let storageDirectory: URL
    private let planDirectory: URL
    private var threadID: UUID?
    private var running: Process?
    private var onQuit: AnyCancellable?
    private var unreadableHistory = false
    private var historyFile: URL { storageDirectory.appendingPathComponent("conversation.json") }

    convenience init() {
        let bundle = Bundle.main
        let path: (String) -> URL? = {
            (bundle.object(forInfoDictionaryKey: $0) as? String).map { URL(fileURLWithPath: $0) }
        }
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        self.init(projectURL: bundle.url(forResource: "Agent", withExtension: nil), codexURL: path("WorktimeCodexPath"),
                  storageDirectory: support.appendingPathComponent("local.worktime-planner.chat"),
                  planDirectory: FileManager.default.homeDirectoryForCurrentUser
                    .appendingPathComponent(".local/share/personal-worktime-agent"))
    }

    init(projectURL: URL?, codexURL: URL?, storageDirectory: URL, planDirectory: URL) {
        self.projectURL = projectURL
        self.codexURL = codexURL
        self.storageDirectory = storageDirectory
        self.planDirectory = planDirectory
        if FileManager.default.fileExists(atPath: historyFile.path) {
            do {
                let history = try JSONDecoder().decode(Transcript.self, from: Data(contentsOf: historyFile))
                messages = history.messages
                threadID = history.threadID
                if messages.last?.role == "user" { errorMessage = "上条消息还没有收到完整回复，可重试。" }
            } catch {
                unreadableHistory = true
                errorMessage = "读取本机聊天记录失败，请检查 \(historyFile.path)。"
            }
        }
        onQuit = NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)
            .sink { [weak self] _ in
                if let process = self?.running, process.isRunning { process.terminate() }
            }
    }

    func send(_ text: String) {
        let content = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !busy, !content.isEmpty, !unreadableHistory else { return }
        messages.append(ChatMessage(role: "user", text: content))
        ask(content)
    }

    func retry() {
        guard !busy, !unreadableHistory, let last = messages.last, last.role == "user" else { return }
        ask(last.text)
    }

    func stop() {
        guard let process = running else { return }
        running = nil
        busy = false
        errorMessage = "已停止等待。你可以重试，也可以发送新消息。"
        let pid = process.processIdentifier
        guard process.isRunning, pid > 1 else { return }
        // Foundation 为子进程创建独立进程组时，一并停止该请求启动的工具。
        if getpgid(pid) == pid && pid != getpgrp() {
            killpg(pid, SIGKILL)
        } else {
            kill(pid, SIGKILL)
        }
    }

    private func save() throws {
        let fs = FileManager.default
        try fs.createDirectory(at: storageDirectory, withIntermediateDirectories: true,
                               attributes: [.posixPermissions: 0o700])
        let history = Transcript(threadID: threadID, messages: messages)
        try JSONEncoder().encode(history).write(to: historyFile, options: .atomic)
        try fs.setAttributes([.posixPermissions: 0o600], ofItemAtPath: historyFile.path)
    }

    private func ask(_ text: String) {
        errorMessage = nil
        do { try save() } catch {
            errorMessage = "无法保存本机聊天记录，请检查磁盘空间和目录权限。"
            return
        }
        let fs = FileManager.default
        guard let project = projectURL, fs.fileExists(atPath: project.appendingPathComponent("AGENTS.md").path) else {
            errorMessage = "找不到工时助手工程。请从工程目录重新安装应用。"
            return
        }
        guard let executable = codexURL, fs.isExecutableFile(atPath: executable.path) else {
            errorMessage = "找不到本机 Codex。请确认 Codex 可用后重新安装工时助手。"
            return
        }
        let process = Process()
        let input = Pipe()
        // CLI 的后代可能继承标准输出。等待 CLI 自身退出，不让管道 EOF 卡住下一次对话。
        guard let temporary = tmpfile() else {
            errorMessage = "无法为本次回复创建临时文件，请检查磁盘空间后重试。"
            return
        }
        let descriptor = dup(fileno(temporary))
        fclose(temporary)
        guard descriptor >= 0 else {
            errorMessage = "无法读取本次回复，请重试。"
            return
        }
        let output = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        process.executableURL = executable
        process.currentDirectoryURL = project
        process.arguments = ["exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "--add-dir", planDirectory.path,
                             "--cd", project.path, "--config", "shell_environment_policy.set.PYTHONDONTWRITEBYTECODE=\"1\"",
                             "--json"]
        if let id = threadID { process.arguments! += ["resume", id.uuidString.lowercased()] }
        process.arguments!.append("-")
        var env = ProcessInfo.processInfo.environment
        let paths = [executable.deletingLastPathComponent().path, "/opt/homebrew/bin", "/usr/local/bin",
                     fs.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin").path,
                     env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"]
        env["PATH"] = paths.joined(separator: ":")
        process.environment = env
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        do {
            try fs.createDirectory(at: planDirectory, withIntermediateDirectories: true,
                                   attributes: [.posixPermissions: 0o700])
            try process.run()
        } catch {
            errorMessage = "无法启动对话：\(error.localizedDescription)"
            return
        }
        running = process
        busy = true
        let instruction = """
        你是用户的个人工时助手。遵循当前工程 AGENTS.md 中的估时、排程和进度反馈流程。仅查看计划时只执行 python3 personal_plan.py show。排程或重排时先遵循项目中钉钉日历读取规则。使用简体中文直接回复，日程用易读的短行展示。本窗口用于帮助用户管理个人工时。
        """
        let prompt = threadID == nil ? instruction + "\n\n用户消息：\n" + text : text
        let writer = input.fileHandleForWriting
        _ = fcntl(writer.fileDescriptor, F_SETNOSIGPIPE, 1)
        DispatchQueue.global(qos: .userInitiated).async {
            defer { try? writer.close() }
            try? writer.write(contentsOf: Data(prompt.utf8))
        }
        Task {
            let result = await Task.detached(priority: .userInitiated) {
                await Self.readTurn(output, process: process)
            }.value
            guard running === process else { return }
            running = nil
            busy = false
            if let id = result.thread { threadID = id }
            if let error = result.problem {
                errorMessage = error
            } else {
                messages.append(ChatMessage(role: "assistant", text: result.answer))
            }
            do { try save() } catch {
                errorMessage = "本次对话仅保留在当前窗口，写入聊天记录失败。"
            }
        }
    }

    private nonisolated static func readTurn(_ stream: FileHandle, process: Process) async
        -> (thread: UUID?, answer: String, problem: String?) {
        var thread: UUID?
        var answer = "", detail = ""
        var completed = false
        process.waitUntilExit()
        do {
            try stream.seek(toOffset: 0)
            for try await line in stream.bytes.lines {
                guard let event = (try? JSONSerialization.jsonObject(with: Data(line.utf8))) as? [String: Any]
                else { continue }
                switch event["type"] as? String {
                case "thread.started":
                    thread = (event["thread_id"] as? String).flatMap(UUID.init(uuidString:))
                case "item.completed":
                    if let item = event["item"] as? [String: Any], item["type"] as? String == "agent_message",
                       let text = item["text"] as? String { answer = text }
                case "turn.completed":
                    completed = true
                case "turn.failed", "error":
                    detail = (event["error"] as? [String: Any])?["message"] as? String
                        ?? event["message"] as? String ?? ""
                default: break
                }
            }
        } catch { completed = false }
        guard process.terminationStatus == 0, completed,
              !answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            let message: String
            if ["unauthorized", "authentication", "401", "log in", "login"].contains(where: detail.lowercased().contains) {
                message = "本机 Codex 需要重新登录，登录后可重试。"
            } else {
                message = "Codex 未返回完整回复。请检查网络和本机 Codex 登录状态后重试。"
            }
            return (thread, "", message)
        }
        return (thread, answer, nil)
    }
}
