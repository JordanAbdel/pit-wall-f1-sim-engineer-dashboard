import SwiftUI

extension Notification.Name {
    static let reloadDashboard = Notification.Name("PitWallReloadDashboard")
}

@main
struct PitWallApp: App {
    @NSApplicationDelegateAdaptor private var appDelegate: AppDelegate
    @StateObject private var engine = Engine()

    var body: some Scene {
        // One window. Closing it keeps the engine (and the voice) running;
        // clicking the Dock icon brings it back. Quit stops the engine.
        Window("PitWall", id: "main") {
            ContentView(engine: engine)
                .frame(minWidth: 960, minHeight: 600)
                .preferredColorScheme(.dark)
                .onAppear {
                    appDelegate.engine = engine
                    engine.start()
                }
        }
        .defaultSize(width: 1440, height: 900)
        .commands {
            CommandGroup(before: .toolbar) {
                Button("Reload Dashboard") {
                    NotificationCenter.default.post(name: .reloadDashboard, object: nil)
                }
                .keyboardShortcut("r")
                Button("Open in Browser") {
                    NSWorkspace.shared.open(engine.dashboardURL)
                }
                Divider()
            }
            CommandGroup(after: .help) {
                Button("Show Engine Log") { engine.showLog() }
            }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    var engine: Engine?

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let engine else { return .terminateNow }
        // Give main.py time to archive the session before the app exits.
        Task {
            await engine.stop()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}
