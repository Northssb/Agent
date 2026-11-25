import AppKit
import SwiftUI

@main
@MainActor
struct WorktimeChatApp: App {
    @NSApplicationDelegateAdaptor(ChatAppDelegate.self) private var delegate
    @StateObject private var session = ChatSession()

    var body: some Scene {
        Window("工时助手", id: "worktime-chat") {
            ChatWindow(session: session)
        }
        .defaultSize(width: 740, height: 620)
    }
}

@MainActor
private final class ChatAppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if let window = sender.windows.first(where: { $0.title == "工时助手" && $0.canBecomeKey }) {
            if window.isMiniaturized { window.deminiaturize(nil) }
            window.makeKeyAndOrderFront(nil)
            return false
        }
        return true
    }
}

private struct ChatWindow: View {
    @ObservedObject var session: ChatSession
    @State private var draft = ""
    @FocusState private var typing: Bool

    private var canSend: Bool {
        !session.busy && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(spacing: 0) {
            if session.messages.isEmpty && !session.busy && session.errorMessage == nil {
                VStack(spacing: 18) {
                    Image(nsImage: NSApplication.shared.applicationIconImage)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(width: 72, height: 72)
                        .accessibilityHidden(true)
                    Text("今天想安排什么工作？")
                        .font(.title2.weight(.medium))
                    HStack(spacing: 10) {
                        suggestion("查看我的计划")
                        suggestion("帮我排明天")
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                conversation
            }

            if let error = session.errorMessage {
                HStack(alignment: .firstTextBaseline, spacing: 9) {
                    Image(systemName: "exclamationmark.circle")
                        .foregroundStyle(.orange)
                    Text(error).font(.callout).textSelection(.enabled)
                    Spacer(minLength: 6)
                    Button("重试") { session.retry() }
                        .disabled(session.busy)
                }
                .padding(.horizontal, 22)
                .padding(.vertical, 8)
            }

            Divider()
            HStack(alignment: .center, spacing: 12) {
                TextField("输入任务、会议或进度…", text: $draft)
                    .textFieldStyle(.plain)
                    .font(.body)
                    .accessibilityLabel("消息")
                    .focused($typing)
                    .onSubmit { sendDraft() }
                Button("发送", action: sendDraft)
                    .buttonStyle(.borderedProminent)
                    .tint(.teal)
                    .disabled(!canSend)
                    .keyboardShortcut(.return, modifiers: [.command])
            }
            .padding(12)
            .background(.background, in: RoundedRectangle(cornerRadius: 12))
            .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(.quaternary) }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)
        }
        .frame(minWidth: 440, minHeight: 360)
        .onAppear { typing = true }
    }

    private func suggestion(_ title: String) -> some View {
        Button(title) {
            draft = title
            typing = true
        }
        .controlSize(.large)
    }

    private func sendDraft() {
        guard canSend else { return }
        session.send(draft.trimmingCharacters(in: .whitespacesAndNewlines))
        draft = ""
        typing = true
    }

    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 20) {
                    ForEach(session.messages) { message in
                        MessageRow(message: message)
                    }
                    if session.busy {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text("正在回复…").font(.callout).foregroundStyle(.secondary)
                            Button("停止") { session.stop() }
                        }
                    }
                    Color.clear.frame(height: 1).id("end-of-chat")
                }
                .padding(22)
            }
            .onAppear { proxy.scrollTo("end-of-chat", anchor: .bottom) }
            .onChange(of: session.messages.count) { _ in
                proxy.scrollTo("end-of-chat", anchor: .bottom)
            }
            .onChange(of: session.busy) { _ in
                proxy.scrollTo("end-of-chat", anchor: .bottom)
            }
        }
    }
}

private struct MessageRow: View {
    let message: ChatMessage

    var body: some View {
        let fromUser = message.role == "user"
        VStack(alignment: .leading, spacing: 6) {
            Text(fromUser ? "你" : "工时助手")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(verbatim: message.text)
                .lineSpacing(4)
                .textSelection(.enabled)
                .padding(.horizontal, fromUser ? 12 : 0)
                .padding(.vertical, fromUser ? 8 : 0)
                .background(fromUser ? Color.teal.opacity(0.12) : .clear,
                            in: RoundedRectangle(cornerRadius: 10))
                .contextMenu {
                    Button("复制全文") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(message.text, forType: .string)
                    }
                }
        }
        .frame(maxWidth: .infinity, alignment: fromUser ? .trailing : .leading)
    }
}
