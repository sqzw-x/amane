// AmaneUI — macOS menu bar companion for Amane.
//
// A stateless NSStatusItem app: polls the local Amane HTTP API.
// The .app's CFBundleExecutable (macapp/Sources/Amane) launches this as a
// sibling of the Python server with `--base-url` and `--watch-parent <app pid>`.

import AppKit
import Foundation

// MARK: - Localization
// 菜单字符串按系统语言 (macOS 惯例), 不跟随前端浏览器语言.
// 无 .lproj 资源: 裸可执行文件, 直接字典映射 zh / 其他一律 en.

private let isChinese = (Locale.preferredLanguages.first ?? "").hasPrefix("zh")

private func tr(_ zh: String, _ en: String) -> String {
    isChinese ? zh : en
}

// MARK: - Arguments
//   --base-url http://127.0.0.1:18000   API root to poll (default shown)
//   --token <token>                     API token for Authorization header
//   --watch-parent [pid]                quit when pid (or getppid()) exits

private func parseArguments() -> (baseURL: String, token: String, parentPid: pid_t) {
    let raw = CommandLine.arguments
    var baseURL = "http://127.0.0.1:18000"
    var token = ""
    var parentPid: pid_t = 0
    var i = 1
    while i < raw.count {
        switch raw[i] {
        case "--base-url":
            i += 1
            if i < raw.count { baseURL = raw[i] }
        case "--token":
            i += 1
            if i < raw.count { token = raw[i] }
        case "--watch-parent":
            if i + 1 < raw.count, let parsed = Int32(raw[i + 1]), parsed > 1 {
                i += 1
                parentPid = parsed
            } else {
                parentPid = getppid()
            }
        default:
            break
        }
        i += 1
    }
    return (baseURL, token, parentPid)
}

final class MenuController: NSObject, NSApplicationDelegate {
    private let baseURL: String
    private let token: String
    private let parentPid: pid_t
    /// 必须在 `applicationDidFinishLaunching` 里创建: 更早调用
    /// `NSStatusBar.statusItem` 时 WindowServer / CGS 尚未就绪, SkyLight
    /// `CGSConnectionByID` 会断言 (SIGABRT). 见 #28.
    private var statusItem: NSStatusItem?
    private var statusLine: NSMenuItem?
    private var dataDirItem: NSMenuItem?
    private var checkUpdateItem: NSMenuItem?
    private var restartItem: NSMenuItem?
    private var pollTimer: Timer?
    private var dataDir = ""
    private var supervised = false

    init(baseURL: String, token: String, parentPid: pid_t) {
        self.baseURL = baseURL
        self.token = token
        self.parentPid = parentPid
        super.init()
    }

    /// 18×18 模板图标: 与 `assets/logo.svg` 同一字标 A (播放字腔).
    /// 模板图只取 alpha, 颜色由系统按菜单栏深/浅色自动着色 (菜单栏不能用彩色徽标).
    /// 注意: NSImage lockFocus 坐标系 y 轴向上, 顶点坐标必须朝上.
    private static func makeTemplateIcon() -> NSImage {
        let image = NSImage(size: NSSize(width: 18, height: 18))
        image.lockFocus()

        // 实心 A + 播放三角字腔, 18 格网格与 logo.svg 一致; 字腔用 even-odd 镂空.
        let path = NSBezierPath()
        path.windingRule = .evenOdd
        path.move(to: NSPoint(x: 9.0, y: 18.0))  // 顶点 (顶部)
        path.line(to: NSPoint(x: 0.0, y: 0.0))  // 左腿 + 底横
        path.line(to: NSPoint(x: 18.0, y: 0.0))
        path.close()
        path.move(to: NSPoint(x: 7.0, y: 9.5))  // 播放三角字腔 (y 向上翻转)
        path.line(to: NSPoint(x: 7.0, y: 3.5))
        path.line(to: NSPoint(x: 12.6, y: 6.5))
        path.close()
        NSColor.black.setFill()
        path.fill()

        image.unlockFocus()
        image.isTemplate = true
        return image
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Menu bar only: no Dock icon, no activation in Cmd-Tab.
        NSApp.setActivationPolicy(.accessory)
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = Self.makeTemplateIcon()
        item.button?.toolTip = "Amane"
        statusItem = item
        buildMenu()
        startPolling()
        if parentPid > 1 {
            watchParentProcess()
        }
    }

    private func buildMenu() {
        let menu = NSMenu()

        let status = NSMenuItem(title: tr("连接中…", "Connecting…"), action: nil, keyEquivalent: "")
        status.isEnabled = false
        menu.addItem(status)
        statusLine = status

        menu.addItem(.separator())

        let open = NSMenuItem(
            title: tr("打开 Web UI", "Open Web UI"), action: #selector(openWebUI), keyEquivalent: "o")
        open.target = self
        menu.addItem(open)

        let data = NSMenuItem(
            title: tr("打开数据目录", "Open Data Directory"), action: #selector(openDataDirectory),
            keyEquivalent: "")
        data.target = self
        data.isEnabled = false
        menu.addItem(data)
        dataDirItem = data

        let copy = NSMenuItem(
            title: tr("复制 API Token", "Copy API Token"), action: #selector(copyToken),
            keyEquivalent: "")
        copy.target = self
        copy.isEnabled = !token.isEmpty
        menu.addItem(copy)

        menu.addItem(.separator())

        let check = NSMenuItem(
            title: tr("检查更新", "Check for Updates"), action: #selector(checkUpdate),
            keyEquivalent: "")
        check.target = self
        check.isEnabled = false
        menu.addItem(check)
        checkUpdateItem = check

        let restart = NSMenuItem(
            title: tr("重启服务器", "Restart Server"), action: #selector(restartServer),
            keyEquivalent: "")
        restart.target = self
        restart.isEnabled = false
        menu.addItem(restart)
        restartItem = restart

        menu.addItem(.separator())

        let quit = NSMenuItem(
            title: tr("退出 Amane", "Quit Amane"), action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)

        statusItem?.menu = menu
    }

    // MARK: - Polling

    private func startPolling() {
        poll()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.poll()
        }
    }

    private func poll() {
        guard let url = URL(string: baseURL + "/api/system/desktop") else { return }
        var request = URLRequest(url: url)
        request.timeoutInterval = 2.0
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        URLSession.shared.dataTask(with: request) { [weak self] data, _, error in
            var connected = false
            var version = ""
            var dataDir = ""
            var supervised = false
            if error == nil, let data {
                if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                    let v = obj["version"] as? String
                {
                    version = v
                    connected = true
                    dataDir = obj["data_dir"] as? String ?? ""
                    supervised = obj["supervised"] as? Bool ?? false
                }
            }
            DispatchQueue.main.async {
                self?.apply(
                    status: connected, version: version, dataDir: dataDir, supervised: supervised)
            }
        }.resume()
    }

    private func apply(status connected: Bool, version: String, dataDir: String, supervised: Bool) {
        self.dataDir = dataDir
        self.supervised = supervised
        if connected {
            statusLine?.title = tr("运行中 · v\(version)", "Running · v\(version)")
            statusItem?.button?.toolTip = tr(
                "Amane 运行中 · v\(version)", "Amane running · v\(version)")
        } else {
            statusLine?.title = tr("未连接", "Disconnected")
            statusItem?.button?.toolTip = tr("Amane 服务未连接", "Amane not connected")
        }
        dataDirItem?.isEnabled = connected && !dataDir.isEmpty
        checkUpdateItem?.isEnabled = connected
        restartItem?.isEnabled = connected && supervised
    }

    // MARK: - Actions

    @objc private func openWebUI() {
        // 打开基础 URL, 不带 token (token 不出现在任何 URL). 首次访问在
        // 前端登录门手动输入 token (localStorage 记住, 每浏览器一次).
        if let url = URL(string: baseURL) {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func copyToken() {
        // 登录门手动输入用: token 进剪贴板, 不出现在任何 URL.
        guard !token.isEmpty else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(token, forType: .string)
    }

    @objc private func openDataDirectory() {
        guard !dataDir.isEmpty else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: dataDir))
    }

    @objc private func checkUpdate() {
        apiJSON(path: "/api/system/release", method: "GET", timeout: 15) { [weak self] obj, err in
            guard let self else { return }
            if err != nil || obj == nil {
                self.alert(
                    tr("检查更新失败", "Update check failed"),
                    tr("暂时无法联系 GitHub，请稍后重试。", "Could not reach GitHub. Try again later."))
                return
            }
            let newer = obj?["newer"] as? Bool ?? false
            if newer, let html = obj?["html_url"] as? String, let url = URL(string: html) {
                NSWorkspace.shared.open(url)
            } else {
                self.alert(
                    tr("已是最新", "Up to date"), tr("当前已是最新版本。", "You are running the latest version.")
                )
            }
        }
    }

    @objc private func restartServer() {
        apiJSON(path: "/api/system/restart", method: "POST", timeout: 5) { [weak self] _, err in
            guard let self, err != nil else { return }
            self.alert(
                tr("重启失败", "Restart failed"), tr("无法请求重启。", "Could not request a restart."))
        }
    }

    private func apiJSON(
        path: String,
        method: String,
        timeout: TimeInterval,
        completion: @escaping ([String: Any]?, Error?) -> Void
    ) {
        guard let url = URL(string: baseURL + path) else { return }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = timeout
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        URLSession.shared.dataTask(with: request) { data, response, error in
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            var obj: [String: Any]?
            if error == nil, let data, !data.isEmpty {
                obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            }
            let httpError: Error? =
                (error == nil && (200..<300).contains(status))
                ? nil
                : (error ?? NSError(domain: "AmaneUI", code: status))
            DispatchQueue.main.async {
                completion(obj, httpError)
            }
        }.resume()
    }

    private func alert(_ title: String, _ message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.runModal()
    }

    @objc private func quitApp() {
        if parentPid > 1 {
            // 应用进程 PID (Swift CFBundleExecutable), 不是 Python.
            kill(parentPid, SIGTERM)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { NSApp.terminate(nil) }
        } else {
            NSApp.terminate(nil)
        }
    }

    // MARK: - Parent death detection

    private func watchParentProcess() {
        let pid = parentPid
        DispatchQueue.global(qos: .background).async {
            let kq = kqueue()
            guard kq >= 0 else { return }
            var event = kevent(
                ident: UInt(pid),
                filter: Int16(EVFILT_PROC),
                flags: UInt16(EV_ADD | EV_ENABLE),
                fflags: UInt32(NOTE_EXIT),
                data: 0,
                udata: nil
            )
            if kevent(kq, &event, 1, nil, 0, nil) == -1 { return }
            var fired = kevent()
            if kevent(kq, nil, 0, &fired, 1, nil) > 0 {
                DispatchQueue.main.async { NSApp.terminate(nil) }
            }
        }
    }
}

// SIGTERM → quit: the Python bridge sends this during graceful shutdown.
let sigtermSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigtermSource.setEventHandler { NSApp.terminate(nil) }
sigtermSource.resume()

// 先建立 NSApplication, 再构造任何会碰 AppKit UI 的对象.
let app = NSApplication.shared
let (baseURL, token, parentPid) = parseArguments()
let controller = MenuController(baseURL: baseURL, token: token, parentPid: parentPid)
app.delegate = controller
app.run()
