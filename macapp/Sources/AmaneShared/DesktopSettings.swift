// 桌面设置文件: 壳在启动 Python 前读取, 用来覆盖内置的冷配置默认值.
// 位置取默认数据目录, 不随 AMANE_DATA_DIR 变动 — 读取该文件必须早于确定数据目录.
// 应用进程 (Amane) 与菜单栏 (AmaneUI) 共用位置、键列表与模板, 只有应用进程实施注入.

import Foundation

/// 提示与菜单文案按系统语言 (zh / 其他一律 en), 与菜单栏一致.
public func localized(_ zh: String, _ en: String) -> String {
    (Locale.preferredLanguages.first ?? "").hasPrefix("zh") ? zh : en
}

public enum DesktopSettings {
    public static let fileName = "desktop.env"

    /// 允许写入设置文件的键. 壳内部键 (监督标记、UI 通路、构建覆盖) 不在其中:
    /// 设置文件不得关闭监督标记或替换可执行文件.
    public static let editableKeys = [
        "AMANE_HOST",
        "AMANE_PORT",
        "AMANE_DATA_DIR",
        "AMANE_LOG_DIR",
        "AMANE_SAFE_DIRS",
        "AMANE_TOKEN",
    ]

    /// 未设置 AMANE_DATA_DIR 时的数据目录; 与壳内置默认值一致.
    public static var defaultDataDir: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Amane", isDirectory: true)
    }

    public static var url: URL {
        defaultDataDir.appendingPathComponent(fileName)
    }

    public static let template = """
        # Amane 桌面设置. 每行一个 KEY=VALUE, 以 # 开头的行为注释.
        # 真实环境变量优先于本文件. 修改后经菜单「重启服务器」生效.
        #
        # 数据目录 (数据库、资源、插件). 修改后不会迁移已有数据.
        # AMANE_DATA_DIR=\(defaultDataDir.path)
        #
        # API 监听端口. 与其它服务冲突时修改.
        # AMANE_PORT=18000
        #
        # 文件浏览器与库路径的边界目录, 逗号分隔; ALLOW_ALL 关闭校验.
        # AMANE_SAFE_DIRS=ALLOW_ALL
        #
        # 可写入本文件的键: \(editableKeys.joined(separator: ", "))
        """

    public struct Parsed {
        public let values: [String: String]
        /// 白名单之外的键 (含拼写错误). 壳提示用户后继续以内置默认值启动.
        public let unknownKeys: [String]
    }

    /// dotenv 子集: KEY=VALUE, 值两侧去除空白与可选引号.
    public static func parse(_ text: String) -> Parsed {
        var values: [String: String] = [:]
        var unknownKeys: [String] = []
        for rawLine in text.split(separator: "\n", omittingEmptySubsequences: true) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }
            guard let separator = line.firstIndex(of: "=") else {
                unknownKeys.append(line)
                continue
            }
            let key = line[..<separator].trimmingCharacters(in: .whitespaces)
            guard editableKeys.contains(key) else {
                unknownKeys.append(key)
                continue
            }
            values[key] = unquoted(line[line.index(after: separator)...])
        }
        return Parsed(values: values, unknownKeys: unknownKeys)
    }

    /// 首次运行写入模板; 已有文件不覆盖.
    public static func createIfMissing() {
        let target = url
        guard !FileManager.default.fileExists(atPath: target.path) else { return }
        try? FileManager.default.createDirectory(
            at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? template.write(to: target, atomically: true, encoding: .utf8)
    }

    private static func unquoted(_ raw: Substring) -> String {
        let value = raw.trimmingCharacters(in: .whitespaces)
        guard value.count >= 2, let first = value.first, let last = value.last,
            first == last, first == "\"" || first == "'"
        else { return value }
        return String(value.dropFirst().dropLast())
    }
}
