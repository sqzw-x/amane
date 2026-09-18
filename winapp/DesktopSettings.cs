// 桌面设置文件: %LOCALAPPDATA%\Amane\desktop.env.
// 位置取默认数据目录, 不随 AMANE_DATA_DIR 变动 — 读取该文件必须早于确定数据目录.
// 契约与 macOS 壳 (macapp/Sources/AmaneShared/DesktopSettings.swift) 一致: 文件名、键列表、模板与解析规则逐条对应.

using System.Text;

namespace Amane;

internal static class DesktopSettings
{
    internal const string FileName = "desktop.env";

    /// 允许写入设置文件的键. 壳内部键 (监督标记、UI 通路、构建覆盖) 不在其中:
    /// 设置文件不得关闭监督标记或替换可执行文件.
    internal static readonly string[] EditableKeys =
    [
        "AMANE_HOST",
        "AMANE_PORT",
        "AMANE_DATA_DIR",
        "AMANE_LOG_DIR",
        "AMANE_SAFE_DIRS",
        "AMANE_TOKEN",
    ];

    /// 未设置 AMANE_DATA_DIR 时的数据目录; 与壳内置默认值一致.
    internal static string DefaultDataDir { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Amane"
    );

    internal static string SettingsPath { get; } = Path.Combine(DefaultDataDir, FileName);

    internal static string Template { get; } =
        $"""
        # Amane 桌面设置. 每行一个 KEY=VALUE, 以 # 开头的行为注释.
        # 真实环境变量优先于本文件. 修改后经菜单「重启服务器」生效.
        #
        # 数据目录 (数据库、资源、插件). 修改后不会迁移已有数据.
        # AMANE_DATA_DIR={DefaultDataDir}
        #
        # API 监听地址. 默认只接受本机连接; 0.0.0.0 或 :: 使局域网可访问, 托盘仍访问本机地址.
        # AMANE_HOST=127.0.0.1
        #
        # API 监听端口. 与其它服务冲突时修改.
        # AMANE_PORT=18000
        #
        # 文件浏览器与库路径的边界目录, 逗号分隔; ALLOW_ALL 关闭校验.
        # AMANE_SAFE_DIRS=ALLOW_ALL
        #
        # 可写入本文件的键: {string.Join(", ", EditableKeys)}
        """;

    internal readonly record struct Parsed(Dictionary<string, string> Values, List<string> UnknownKeys);

    /// dotenv 子集: KEY=VALUE, 值两侧去除空白与可选引号.
    internal static Parsed Parse(string text)
    {
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        var unknown = new List<string>();
        foreach (var rawLine in text.Split('\n'))
        {
            var line = rawLine.Trim();
            if (line.Length == 0 || line.StartsWith('#'))
            {
                continue;
            }

            var separator = line.IndexOf('=');
            if (separator < 0)
            {
                unknown.Add(line);
                continue;
            }

            var key = line[..separator].Trim();
            if (!EditableKeys.Contains(key, StringComparer.Ordinal))
            {
                // 白名单之外的键 (含拼写错误): 提示用户后忽略, 不写入子进程环境.
                unknown.Add(key);
                continue;
            }

            values[key] = Unquoted(line[(separator + 1)..]);
        }

        return new Parsed(values, unknown);
    }

    /// 首次运行写入模板; 已有文件不覆盖.
    internal static void CreateIfMissing()
    {
        if (File.Exists(SettingsPath))
        {
            return;
        }

        try
        {
            Directory.CreateDirectory(DefaultDataDir);
            File.WriteAllText(SettingsPath, Template, new UTF8Encoding(false));
        }
        catch (Exception)
        {
            // 数据目录不可写时按无设置处理; 服务仍以内置默认值启动.
        }
    }

    private static string Unquoted(string raw)
    {
        var value = raw.Trim();
        if (value.Length < 2)
        {
            return value;
        }

        var first = value[0];
        if ((first == '"' || first == '\'') && value[^1] == first)
        {
            return value[1..^1];
        }

        return value;
    }
}
