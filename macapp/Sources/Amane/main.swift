// Amane — CFBundleExecutable.
// Launch Services 身份 (com.github.sqzw-x.amane) + 监督 onedir Python (amane.server)
// + 菜单栏 UI 兄弟进程 (AmaneUI.app, bundle id 不同, 服务重启时 UI 不必跟着死).

import AppKit
import Darwin
import Foundation

private let restartDelay: TimeInterval = {
    if let raw = ProcessInfo.processInfo.environment["AMANE_RESTART_DELAY"],
        let value = TimeInterval(raw), value >= 0
    {
        return value
    }
    return 2
}()

final class Launcher: NSObject, NSApplicationDelegate {
    private let lock = NSLock()
    private var python: Process?
    private var ui: Process?
    private var stopping = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        Self.prepareDesktopEnv()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.supervisePython()
            DispatchQueue.main.async { NSApp.terminate(nil) }
        }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            self?.babysitUI()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopAll(killAfter: 5)
    }

    // MARK: - Python

    private func supervisePython() {
        guard let bin = Self.serverBinary() else { exit(1) }
        while true {
            if isStopping { return }
            let proc = Process()
            proc.executableURL = bin
            proc.arguments = Array(CommandLine.arguments.dropFirst())
            proc.currentDirectoryURL = bin.deletingLastPathComponent()
            // 菜单栏由本进程拉起; 子进程带此标记, 避免 Python 再 spawn 一份.
            var env = ProcessInfo.processInfo.environment
            env["AMANE_UI_DISABLED"] = "1"
            proc.environment = env
            do {
                try proc.run()
            } catch {
                exit(127)
            }
            setPython(proc)
            proc.waitUntilExit()
            setPython(nil)
            if isStopping { return }
            switch proc.terminationStatus {
            case 0, 130, 143: return
            case 3: continue
            case 126, 127: exit(proc.terminationStatus)
            default:
                let deadline = Date().addingTimeInterval(restartDelay)
                while !isStopping && Date() < deadline {
                    Thread.sleep(forTimeInterval: 0.05)
                }
            }
        }
    }

    // MARK: - UI (sibling of Python, child of this process)

    private func babysitUI() {
        if ProcessInfo.processInfo.environment["AMANE_UI_DISABLED"] == "1" { return }
        guard Self.uiBinary() != nil else { return }
        var failures = 0
        let backoff: [TimeInterval] = [1, 3, 10]
        while !isStopping {
            spawnUI()
            lock.lock()
            let proc = ui
            lock.unlock()
            guard let proc else {
                Thread.sleep(forTimeInterval: 1)
                continue
            }
            proc.waitUntilExit()
            if isStopping { return }
            let delay = backoff[min(failures, backoff.count - 1)]
            failures += 1
            let deadline = Date().addingTimeInterval(delay)
            while !isStopping && Date() < deadline {
                Thread.sleep(forTimeInterval: 0.05)
            }
        }
    }

    private func spawnUI() {
        lock.lock()
        let already = ui?.isRunning == true
        lock.unlock()
        if already { return }
        guard let bin = Self.uiBinary() else { return }
        if isStopping { return }
        let env = ProcessInfo.processInfo.environment
        let host = env["AMANE_HOST"] ?? "127.0.0.1"
        let port = env["AMANE_PORT"] ?? "18000"
        var argv = ["--base-url", "http://\(host):\(port)", "--watch-parent", String(getpid())]
        if env["AMANE_TOKEN"] == "off" {
            // 关鉴权: 不带 --token
        } else if let explicit = env["AMANE_TOKEN"], !explicit.isEmpty {
            argv += ["--token", explicit]
        } else if let token = waitForTokenFile() {
            argv += ["--token", token]
        } else {
            return
        }
        let proc = Process()
        proc.executableURL = bin
        proc.arguments = argv
        do {
            try proc.run()
        } catch {
            return
        }
        lock.lock()
        ui = proc
        lock.unlock()
    }

    /// bootstrap 把 token 写到 data_dir/token; 未写入前阻塞, 停机则 nil.
    private func waitForTokenFile() -> String? {
        let path = Self.dataDir().appendingPathComponent("token")
        while !isStopping {
            if let raw = try? String(contentsOf: path, encoding: .utf8) {
                let token = raw.trimmingCharacters(in: .whitespacesAndNewlines)
                if !token.isEmpty { return token }
            }
            Thread.sleep(forTimeInterval: 0.1)
        }
        return nil
    }

    // MARK: - Stop / state

    private var isStopping: Bool {
        lock.lock()
        defer { lock.unlock() }
        return stopping
    }

    private func setPython(_ proc: Process?) {
        lock.lock()
        python = proc
        lock.unlock()
    }

    private func stopAll(killAfter seconds: TimeInterval) {
        lock.lock()
        stopping = true
        let kids = [python, ui].compactMap { $0 }
        lock.unlock()
        for proc in kids where proc.isRunning {
            proc.terminate()
        }
        let deadline = Date().addingTimeInterval(seconds)
        for proc in kids {
            while proc.isRunning && Date() < deadline {
                Thread.sleep(forTimeInterval: 0.05)
            }
            if proc.isRunning {
                kill(proc.processIdentifier, SIGKILL)
            }
        }
    }

    // MARK: - Paths / env

    private static func prepareDesktopEnv() {
        setenv("PYDANTIC_DISABLE_PLUGINS", "1", 0)
        setenv("AMANE_SUPERVISED", "1", 1)
        setenv("AMANE_HOST", "127.0.0.1", 0)
        setenv("AMANE_PORT", "18000", 0)
        setenv("AMANE_SAFE_DIRS", "ALLOW_ALL", 0)
        let data = dataDir()
        let logs = data.appendingPathComponent("logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        setenv("AMANE_DATA_DIR", data.path, 0)
        setenv("AMANE_LOG_DIR", logs.path, 0)
        if let web = Bundle.main.resourceURL?
            .appendingPathComponent("web/dist/index.html"),
            FileManager.default.isReadableFile(atPath: web.path)
        {
            setenv("AMANE_WEB_DIST", web.deletingLastPathComponent().path, 1)
        }
    }

    private static func dataDir() -> URL {
        if let override = ProcessInfo.processInfo.environment["AMANE_DATA_DIR"], !override.isEmpty {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("Amane", isDirectory: true)
    }

    private static func serverBinary() -> URL? {
        if let override = ProcessInfo.processInfo.environment["AMANE_BIN"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        guard let resources = Bundle.main.resourceURL else { return nil }
        let onedir = resources.appendingPathComponent("onedir", isDirectory: true)
        let primary = onedir.appendingPathComponent("Amane")
        if FileManager.default.isExecutableFile(atPath: primary.path) {
            return primary
        }
        guard
            let items = try? FileManager.default.contentsOfDirectory(
                at: onedir, includingPropertiesForKeys: nil)
        else { return nil }
        return items.first { FileManager.default.isExecutableFile(atPath: $0.path) }
    }

    private static func uiBinary() -> URL? {
        if ProcessInfo.processInfo.environment["AMANE_UI_DISABLED"] == "1" { return nil }
        if let override = ProcessInfo.processInfo.environment["AMANE_UI_BINARY"], !override.isEmpty {
            let url = URL(fileURLWithPath: override)
            return FileManager.default.isExecutableFile(atPath: url.path) ? url : nil
        }
        guard
            let url = Bundle.main.resourceURL?
                .appendingPathComponent("AmaneUI.app/Contents/MacOS/AmaneUI"),
            FileManager.default.isExecutableFile(atPath: url.path)
        else { return nil }
        return url
    }
}

signal(SIGTERM, SIG_IGN)
let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigterm.setEventHandler { NSApp.terminate(nil) }
sigterm.resume()

let launcher = Launcher()
let app = NSApplication.shared
app.delegate = launcher
app.run()
