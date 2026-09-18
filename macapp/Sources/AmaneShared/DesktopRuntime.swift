// 桌面运行期契约: 由绑定地址推导的访问地址, 以及启动失败状态文件.
// 绑定地址与访问地址不同 — AMANE_HOST 是 bind 语义, 可能是通配地址或 IPv6 字面量,
// 两者都不能直接用于 URL. 状态文件写在设置文件旁: 菜单栏是独立进程, 与应用进程之间
// 没有其它通道, 启动失败原因经该文件传递.

import Foundation

public enum DesktopRuntime {
    /// 最近一次启动失败的原因; 文件缺失表示没有待展示的失败.
    public static let statusFileName = "server-status"

    public static var statusURL: URL {
        DesktopSettings.defaultDataDir.appendingPathComponent(statusFileName)
    }

    /// 访问地址的主机部分: 通配地址不是可访问地址, IPv6 字面量在 URL 中必须加方括号.
    public static func accessHost(_ host: String) -> String {
        let trimmed = host.trimmingCharacters(in: .whitespaces)
        let bare =
            trimmed.hasPrefix("[") && trimmed.hasSuffix("]")
            ? String(trimmed.dropFirst().dropLast()) : trimmed
        if bare.isEmpty || bare == "0.0.0.0" || bare == "::" {
            return "localhost"
        }
        return bare.contains(":") ? "[\(bare)]" : bare
    }

    /// 写入失败原因; 传 nil 或空串表示删除状态文件.
    public static func writeStatus(_ reason: String?) {
        let target = statusURL
        guard let reason, !reason.isEmpty else {
            try? FileManager.default.removeItem(at: target)
            return
        }
        try? FileManager.default.createDirectory(
            at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? reason.write(to: target, atomically: true, encoding: .utf8)
    }

    public static func readStatus() -> String? {
        guard let raw = try? String(contentsOf: statusURL, encoding: .utf8) else { return nil }
        let reason = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return reason.isEmpty ? nil : reason
    }
}
