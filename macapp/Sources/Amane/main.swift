// Amane — CFBundleExecutable.
// Launch Services 身份 (com.github.sqzw-x.amane) + 监督 onedir Python (amane.server)
// + 菜单栏 UI 兄弟进程 (AmaneUI.app, bundle id 不同, 服务重启时 UI 不必跟着死).

import AmaneShared
import AppKit
import Darwin
import Foundation

/// 用户真实环境变量快照. 设置文件与内置默认值都不覆盖它.
private let launchEnvironment = ProcessInfo.processInfo.environment

private let restartDelay: TimeInterval = {
    if let raw = ProcessInfo.processInfo.environment["AMANE_RESTART_DELAY"],
        let value = TimeInterval(raw), value >= 0
    {
        return value
    }
    return 2
}()

/// 桌面环境变量的生效值. 每次启动 Python 前重新求解 真实环境变量 > 设置文件 > 内置默认值,
/// 因此菜单「重启服务器」即可应用设置文件的修改. 求解幂等, 不修改本进程环境.
private struct DesktopEnvironment {
    let host: String
    let port: String
    let dataDir: URL
    let token: String?
    /// 注入 Python 子进程的键值, 含壳内部键.
    let pythonEnv: [String: String]
    /// 设置文件中白名单之外的键.
    let unknownKeys: [String]

    var baseURL: String { "http://\(DesktopRuntime.accessHost(host)):\(port)" }
    /// UI 兄弟进程的身份: base-url、token 与 token 文件所在的数据目录都在它启动时固定.
    var uiIdentity: String { "\(baseURL)|\(token ?? "")|\(dataDir.path)" }

    static func resolve() -> DesktopEnvironment {
        DesktopSettings.createIfMissing()
        let text = (try? String(contentsOf: DesktopSettings.url, encoding: .utf8)) ?? ""
        let parsed = DesktopSettings.parse(text)
        let host = value("AMANE_HOST", parsed) ?? "127.0.0.1"
        let port = value("AMANE_PORT", parsed) ?? "18000"
        let dataDir = URL(
            fileURLWithPath: value("AMANE_DATA_DIR", parsed) ?? DesktopSettings.defaultDataDir.path,
            isDirectory: true)
        let logDir = URL(
            fileURLWithPath: value("AMANE_LOG_DIR", parsed)
                ?? dataDir.appendingPathComponent("logs", isDirectory: true).path,
            isDirectory: true)
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        let token = value("AMANE_TOKEN", parsed)
        var pythonEnv = [
            "AMANE_HOST": host,
            "AMANE_PORT": port,
            "AMANE_DATA_DIR": dataDir.path,
            "AMANE_LOG_DIR": logDir.path,
            "AMANE_SAFE_DIRS": value("AMANE_SAFE_DIRS", parsed) ?? "ALLOW_ALL",
            "AMANE_SUPERVISED": "1",
            // 菜单栏由本进程拉起; 子进程带此标记, 避免 Python 再 spawn 一份.
            "AMANE_UI_DISABLED": "1",
            "PYDANTIC_DISABLE_PLUGINS": launchEnvironment["PYDANTIC_DISABLE_PLUGINS"] ?? "1",
        ]
        if let token { pythonEnv["AMANE_TOKEN"] = token }
        if let web = webDist() { pythonEnv["AMANE_WEB_DIST"] = web }
        return DesktopEnvironment(
            host: host, port: port, dataDir: dataDir, token: token, pythonEnv: pythonEnv,
            unknownKeys: parsed.unknownKeys)
    }

    /// 真实环境变量优先, 其次设置文件; 空值按未设置处理.
    private static func value(_ key: String, _ parsed: DesktopSettings.Parsed) -> String? {
        if let explicit = launchEnvironment[key], !explicit.isEmpty { return explicit }
        if let configured = parsed.values[key], !configured.isEmpty { return configured }
        return nil
    }

    private static func webDist() -> String? {
        guard
            let web = Bundle.main.resourceURL?.appendingPathComponent("web/dist/index.html"),
            FileManager.default.isReadableFile(atPath: web.path)
        else { return nil }
        return web.deletingLastPathComponent().path
    }
}

/// 服务进程的语义退出码, 取值与 `amane.server` 一致 (POSIX 的 130 / 143 / 126 / 127 不在此列).
private enum ServerExit {
    /// 请求重启: 由 `POST /api/system/restart` 触发, UI 进程继续存活.
    static let restart: Int32 = 3
    /// 启动失败: 地址无法绑定、端口被占用、配置非法. 原因取自服务输出.
    static let startupFailed: Int32 = 4
}

/// 服务子进程的输出: 读到即转发到本进程标准输出, 并保留末尾若干行.
/// 启动失败时服务只留一行 uvicorn 错误, 退出码本身不携带原因.
private final class ProcessOutput {
    private let pipe = Pipe()
    private let lock = NSLock()
    private var pending = Data()
    private var lines: [String] = []
    private let limit = 40

    func attach(to proc: Process) {
        proc.standardOutput = pipe
        proc.standardError = pipe
    }

    func startReading() {
        let handle = pipe.fileHandleForReading
        DispatchQueue.global(qos: .utility).async { [self] in
            while true {
                let chunk = handle.availableData
                if chunk.isEmpty { break }
                FileHandle.standardOutput.write(chunk)
                consume(chunk)
            }
            try? handle.close()
        }
    }

    /// 关闭本进程持有的写端: 服务退出后读取线程才能读到 EOF, 否则每次重启残留一个线程.
    func closeParentWriteEnd() {
        try? pipe.fileHandleForWriting.close()
    }

    /// 失败原因: 末尾最后一条 ERROR 行; 没有 ERROR 行时取最后一条非空行.
    func failureReason() -> String? {
        lock.lock()
        defer { lock.unlock() }
        let trimmed = lines.map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        return trimmed.last { $0.contains("ERROR") } ?? trimmed.last
    }

    private func consume(_ chunk: Data) {
        var complete: [String] = []
        lock.lock()
        pending.append(chunk)
        while let index = pending.firstIndex(of: 0x0A) {
            complete.append(String(decoding: pending[pending.startIndex..<index], as: UTF8.self))
            pending.removeSubrange(pending.startIndex...index)
        }
        lines.append(contentsOf: complete)
        if lines.count > limit { lines.removeFirst(lines.count - limit) }
        lock.unlock()
    }
}

final class Launcher: NSObject, NSApplicationDelegate {
    private let lock = NSLock()
    private var python: Process?
    private var ui: Process?
    /// 运行中的 UI 进程的身份; 与最新求解结果不一致时须重建该进程.
    private var uiIdentity: String?
    private var stopping = false
    /// 已提示过的非法键, 仅在主队列读写.
    private var warnedKeys: [String] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
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
            let desktop = DesktopEnvironment.resolve()
            warnUnknownKeys(desktop.unknownKeys)
            // 设置文件改了 base-url / token 来源时, 运行中的 UI 仍带着旧 argv: 结束它, 由 babysitUI
            // 以新 argv 重启. 菜单的「打开 Web UI」与「复制 API Token」读的都是那里的值.
            terminateUIIfStale(desktop)
            let proc = Process()
            proc.executableURL = bin
            proc.arguments = Array(CommandLine.arguments.dropFirst())
            proc.currentDirectoryURL = bin.deletingLastPathComponent()
            var env = ProcessInfo.processInfo.environment
            for (key, value) in desktop.pythonEnv {
                env[key] = value
            }
            proc.environment = env
            let output = ProcessOutput()
            output.attach(to: proc)
            // 清除上一次的失败原因; 菜单栏在轮询失败时才读取该文件.
            DesktopRuntime.writeStatus(nil)
            do {
                try proc.run()
            } catch {
                exit(127)
            }
            output.startReading()
            output.closeParentWriteEnd()
            setPython(proc)
            proc.waitUntilExit()
            setPython(nil)
            if isStopping { return }
            switch proc.terminationStatus {
            case 0, 130, 143: return
            case ServerExit.restart: continue
            case 126, 127: exit(proc.terminationStatus)
            default:
                // 启动失败与其它异常退出都退避后重试; 前者把原因留在设置文件旁供菜单栏展示.
                if proc.terminationStatus == ServerExit.startupFailed {
                    let reason = output.failureReason() ?? localized("无输出", "no output")
                    DesktopRuntime.writeStatus(reason)
                }
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
            spawnUI(DesktopEnvironment.resolve())
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

    private func spawnUI(_ desktop: DesktopEnvironment) {
        lock.lock()
        let already = ui?.isRunning == true
        lock.unlock()
        if already { return }
        guard let bin = Self.uiBinary() else { return }
        if isStopping { return }
        var argv = ["--base-url", desktop.baseURL, "--watch-parent", String(getpid())]
        if desktop.token == "off" {
            // 关鉴权: 不带 --token
        } else if let explicit = desktop.token {
            argv += ["--token", explicit]
        } else if let token = waitForTokenFile(desktop.dataDir) {
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
        uiIdentity = desktop.uiIdentity
        lock.unlock()
    }

    /// bootstrap 把 token 写到 data_dir/token; 未写入前阻塞, 停机则 nil.
    private func waitForTokenFile(_ dataDir: URL) -> String? {
        let path = dataDir.appendingPathComponent("token")
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

    /// 求解结果与运行中的 UI 身份不一致时结束该进程; 由 babysitUI 以新 argv 重启.
    private func terminateUIIfStale(_ desktop: DesktopEnvironment) {
        lock.lock()
        let stale = uiIdentity != nil && uiIdentity != desktop.uiIdentity
        lock.unlock()
        if stale {
            stopUI(killAfter: 5)
        }
    }

    /// 结束 UI 兄弟进程并等待退出; 调用方随后以新 argv 重启它.
    private func stopUI(killAfter seconds: TimeInterval) {
        lock.lock()
        let proc = ui
        lock.unlock()
        guard let proc, proc.isRunning else { return }
        proc.terminate()
        let deadline = Date().addingTimeInterval(seconds)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
        }
    }

    // MARK: - Warnings

    /// 白名单之外的键提示一次; 设置文件再次引入新键时重新提示.
    private func warnUnknownKeys(_ keys: [String]) {
        guard !keys.isEmpty else { return }
        DispatchQueue.main.async {
            guard keys != self.warnedKeys else { return }
            self.warnedKeys = keys
            NSApp.activate(ignoringOtherApps: true)
            let alert = NSAlert()
            alert.messageText = localized(
                "桌面设置文件包含无法识别的键", "Unknown keys in the desktop settings file")
            alert.informativeText = localized(
                "以下键将被忽略:\n\(keys.joined(separator: "\n"))\n\n文件: \(DesktopSettings.url.path)",
                "These keys are ignored:\n\(keys.joined(separator: "\n"))\n\nFile: \(DesktopSettings.url.path)"
            )
            alert.runModal()
        }
    }

    // MARK: - Paths

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
