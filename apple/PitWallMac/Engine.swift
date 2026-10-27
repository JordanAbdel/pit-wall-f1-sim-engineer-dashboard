import AppKit
import Foundation

/// Runs the PitWall engine (main.py: telemetry listener, voice, dashboard
/// server) as a child process and reports when its dashboard answers.
/// Quitting stops it with SIGINT so it archives the session first, the
/// same contract the old AppleScript launcher kept.
@MainActor
final class Engine: ObservableObject {
    enum Status: Equatable {
        case starting
        case running
        case failed(String)
    }

    @Published private(set) var status: Status = .starting

    let dashboardPort: Int
    /// User state (driver_db.json, auth.json, logs). Never inside the
    /// bundle, which is signed and may be read-only.
    let homeDir: URL
    private let extraArgs: [String]
    private var process: Process?
    private var starting = false
    private var stopping = false

    var dashboardURL: URL { URL(string: "http://127.0.0.1:\(dashboardPort)/")! }
    var logURL: URL { homeDir.appending(path: "logs/app-console.log") }

    init() {
        // Overrides for development runs (sim.py on other ports, a scratch
        // state folder); a normal launch sets none of these.
        let env = ProcessInfo.processInfo.environment
        dashboardPort = env["PITWALL_DASHBOARD_PORT"].flatMap(Int.init) ?? 8777
        extraArgs = env["PITWALL_ENGINE_ARGS"]?.split(separator: " ").map(String.init) ?? []
        if let home = env["PITWALL_HOME"], !home.isEmpty {
            homeDir = URL(fileURLWithPath: (home as NSString).expandingTildeInPath)
        } else {
            homeDir = FileManager.default
                .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appending(path: "PitWall")
        }
    }

    func start() {
        guard !starting, process == nil, status != .running else { return }
        starting = true
        stopping = false
        status = .starting
        Task {
            defer { starting = false }
            // An engine that is already up (an older PitWall launcher, or a
            // dev run) owns the UDP port; show it instead of failing to bind.
            if await dashboardResponds() {
                status = .running
                return
            }
            do {
                try launch()
            } catch {
                status = .failed(error.localizedDescription)
                return
            }
            await waitUntilReady()
        }
    }

    /// SIGINT lets main.py archive the session; SIGTERM if it hangs 10 s.
    func stop() async {
        guard let process, process.isRunning else { return }
        stopping = true
        process.interrupt()
        for _ in 0..<50 {
            if !process.isRunning { return }
            try? await Task.sleep(for: .milliseconds(200))
        }
        process.terminate()
    }

    func showLog() {
        NSWorkspace.shared.activateFileViewerSelecting([logURL])
    }

    // MARK: - Process

    private func launch() throws {
        let (exe, args, cwd) = try engineCommand()
        try FileManager.default.createDirectory(
            at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let log = try FileHandle(forWritingTo: logURL)

        let p = Process()
        p.executableURL = exe
        p.arguments = args + ["--no-browser", "--dashboard-port", String(dashboardPort)] + extraArgs
        p.currentDirectoryURL = cwd
        var env = ProcessInfo.processInfo.environment
        env["PITWALL_HOME"] = homeDir.path
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        p.standardInput = FileHandle.nullDevice
        p.standardOutput = log
        p.standardError = log
        p.terminationHandler = { [weak self] proc in
            let code = proc.terminationStatus
            Task { @MainActor in self?.engineExited(code) }
        }
        try p.run()
        process = p
    }

    /// The frozen engine embed_engine.sh bundled, or, in a Debug build,
    /// main.py from the source checkout's build-venv.
    private func engineCommand() throws -> (URL, [String], URL) {
        let fm = FileManager.default
        if let resources = Bundle.main.resourceURL {
            let dir = resources.appending(path: "PitWall")
            let exe = dir.appending(path: "PitWall")
            if fm.isExecutableFile(atPath: exe.path) {
                return (exe, [], dir)
            }
        }
        if let src = Bundle.main.object(forInfoDictionaryKey: "PitWallSourceDir") as? String,
           !src.isEmpty {
            let dir = URL(fileURLWithPath: src).standardizedFileURL
            let python = dir.appending(path: "build-venv/bin/python")
            if fm.isExecutableFile(atPath: python.path) {
                return (python, ["-u", "main.py"], dir)
            }
        }
        throw EngineError("This copy of PitWall is missing its engine. Download PitWall again.")
    }

    private func waitUntilReady() async {
        for _ in 0..<240 {  // 60 s; the dashboard comes up before the voice loads
            guard let process, process.isRunning else { return }  // engineExited reports it
            if await dashboardResponds() {
                status = .running
                return
            }
            try? await Task.sleep(for: .milliseconds(250))
        }
        status = .failed("The engine didn't start within a minute.\n\n" + logTail())
    }

    private func engineExited(_ code: Int32) {
        process = nil
        if stopping { return }
        status = .failed("The engine stopped (exit code \(code)).\n\n" + logTail())
    }

    private func dashboardResponds() async -> Bool {
        var request = URLRequest(url: dashboardURL.appending(path: "config.json"), timeoutInterval: 1)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        guard let (_, response) = try? await URLSession.shared.data(for: request) else { return false }
        return (response as? HTTPURLResponse)?.statusCode == 200
    }

    private func logTail(lines: Int = 8) -> String {
        guard let text = try? String(contentsOf: logURL, encoding: .utf8) else { return "" }
        return text.split(separator: "\n", omittingEmptySubsequences: true)
            .suffix(lines).joined(separator: "\n")
    }
}

struct EngineError: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}
