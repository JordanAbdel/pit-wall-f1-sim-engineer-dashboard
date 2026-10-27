import SwiftUI

/// The dashboard's own background (--ink-0), so nothing flashes white
/// while the engine and page load.
let dashboardBackground = Color(red: 0x0A / 255, green: 0x0A / 255, blue: 0x09 / 255)

struct ContentView: View {
    @ObservedObject var engine: Engine
    @State private var pageLoaded = false

    var body: some View {
        ZStack {
            dashboardBackground
            if engine.status == .running {
                DashboardWebView(url: engine.dashboardURL, loaded: $pageLoaded)
                    .opacity(pageLoaded ? 1 : 0)
            }
            switch engine.status {
            case .starting:
                StartingView()
            case .running:
                if !pageLoaded { StartingView() }
            case .failed(let message):
                FailedView(message: message, retry: engine.start, showLog: engine.showLog)
            }
        }
        .ignoresSafeArea()
        .onChange(of: engine.status) { _, status in
            if status != .running { pageLoaded = false }
        }
    }
}

private struct StartingView: View {
    var body: some View {
        VStack(spacing: 14) {
            ProgressView().controlSize(.small)
            Text("Starting PitWall…")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.secondary)
        }
    }
}

private struct FailedView: View {
    let message: String
    let retry: () -> Void
    let showLog: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("PitWall couldn't start")
                .font(.system(size: 20, weight: .semibold))
            Text("If PitWall is already running in another window or terminal, quit it first.")
                .foregroundStyle(.secondary)
            ScrollView {
                Text(message)
                    .font(.system(size: 11, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
            }
            .frame(maxHeight: 220)
            .background(.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 8))
            HStack {
                Button("Try Again", action: retry).keyboardShortcut(.defaultAction)
                Button("Show Log", action: showLog)
            }
        }
        .frame(maxWidth: 560)
        .padding(32)
    }
}
