// Amane — Windows desktop shell.
// One WinExe: tray (NotifyIcon) + supervise frozen amane.server. Python only runs HTTP.

using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net.Http.Headers;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace Amane;

internal static class Program
{
    private const string MutexName = @"Local\com.github.sqzw-x.amane";

    [STAThread]
    private static int Main()
    {
        Mutex? mutex = null;
        var createdNew = false;
        try
        {
            mutex = new Mutex(true, MutexName, out createdNew);
        }
        catch (AbandonedMutexException ex)
        {
            mutex = ex.Mutex;
            createdNew = true;
        }

        using (mutex)
        {
            if (!createdNew)
            {
                return 0;
            }

            return App.Run();
        }
    }
}

internal sealed class App
{
    private const uint IdOpen = 1001;
    private const uint IdDataDir = 1002;
    private const uint IdCopy = 1003;
    private const uint IdUpdate = 1004;
    private const uint IdRestart = 1005;
    private const uint IdQuit = 1006;
    private const uint StatusControlCExit = 0xC000013A;
    private const int ExitRestart = 3;
    private const nuint PollTimerId = 1;

    private static readonly bool Zh = Native.IsChineseUi();
    private static App? _instance;
    private static Native.WndProc? _wndProc;

    private readonly object _gate = new();
    private readonly ConcurrentQueue<Action> _ui = new();
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(20) };
    private readonly bool _uiOnly;
    private readonly TimeSpan _restartDelay;

    private nint _hwnd;
    private nint _hIcon;
    private nint _hMenu;
    private nint _hJob;
    private Native.NotifyIconData _nid;
    private uint _taskbarCreated;
    private Process? _python;
    private bool _stopping;
    private bool _pollInFlight;
    private string _baseUrl = "http://127.0.0.1:18000";
    private string _token = "";
    private string _dataDir = "";
    private string _version = "";
    private bool _connected;
    private bool _supervised;
    private bool _trayAdded;
    private bool _unwinding;

    private App()
    {
        _uiOnly = Env("AMANE_UI_ONLY") == "1";
        _restartDelay = TimeSpan.FromSeconds(2);
        if (double.TryParse(Env("AMANE_RESTART_DELAY"), out var delay) && delay >= 0)
        {
            _restartDelay = TimeSpan.FromSeconds(delay);
        }
    }

    internal static int Run()
    {
        var app = new App();
        _instance = app;
        return app.RunLoop();
    }

    private int RunLoop()
    {
        // Manifest is the real declaration; this is a Native AOT fallback before any HWND.
        Native.SetProcessDpiAwarenessContext(Native.DpiAwarenessContextPerMonitorAwareV2);

        if (!_uiOnly)
        {
            PrepareDesktopEnv();
        }

        var host = Env("AMANE_HOST") ?? "127.0.0.1";
        var port = Env("AMANE_PORT") ?? (_uiOnly ? "8000" : "18000");
        _baseUrl = $"http://{host}:{port}";
        ResolveTokenAtStart();

        if (!CreateUi())
        {
            return 1;
        }

        if (!_uiOnly)
        {
            _hJob = CreateKillOnCloseJob();
            _ = Task.Factory.StartNew(SupervisePython, TaskCreationOptions.LongRunning);
        }

        Native.SetTimer(_hwnd, PollTimerId, 3000, 0);
        QueuePoll();

        while (Native.GetMessage(out var msg, 0, 0, 0))
        {
            Native.TranslateMessage(in msg);
            Native.DispatchMessage(in msg);
        }

        Teardown();
        return 0;
    }

    // MARK: - Env / paths

    private static void PrepareDesktopEnv()
    {
        SetDefault("PYDANTIC_DISABLE_PLUGINS", "1");
        Environment.SetEnvironmentVariable("AMANE_SUPERVISED", "1");
        SetDefault("AMANE_HOST", "127.0.0.1");
        SetDefault("AMANE_PORT", "18000");
        SetDefault("AMANE_SAFE_DIRS", "ALLOW_ALL");
        var data = DataDir();
        var logs = Path.Combine(data, "logs");
        Directory.CreateDirectory(logs);
        SetDefault("AMANE_DATA_DIR", data);
        SetDefault("AMANE_LOG_DIR", logs);
        var web = Path.Combine(AppContext.BaseDirectory, "web", "dist", "index.html");
        if (File.Exists(web))
        {
            Environment.SetEnvironmentVariable("AMANE_WEB_DIST", Path.GetDirectoryName(web));
        }
    }

    private static string DataDir()
    {
        var overrideDir = Env("AMANE_DATA_DIR");
        if (!string.IsNullOrEmpty(overrideDir))
        {
            return overrideDir;
        }

        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Amane"
        );
    }

    private static string? ServerBinary()
    {
        var overrideBin = Env("AMANE_BIN");
        if (!string.IsNullOrEmpty(overrideBin))
        {
            return File.Exists(overrideBin) ? overrideBin : null;
        }

        var onedir = Path.Combine(AppContext.BaseDirectory, "onedir");
        foreach (var name in new[] { "Amane.Server.exe", "Amane.exe" })
        {
            var candidate = Path.Combine(onedir, name);
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        return null;
    }

    private void ResolveTokenAtStart()
    {
        var envToken = Env("AMANE_TOKEN");
        if (envToken == "off")
        {
            _token = "";
            return;
        }

        if (!string.IsNullOrEmpty(envToken))
        {
            _token = envToken;
            return;
        }

        if (_uiOnly)
        {
            return;
        }

        _ = Task.Run(WaitForTokenFile);
    }

    private void WaitForTokenFile()
    {
        var path = Path.Combine(DataDir(), "token");
        while (!IsStopping)
        {
            try
            {
                if (File.Exists(path))
                {
                    var token = File.ReadAllText(path).Trim();
                    if (token.Length > 0)
                    {
                        _token = token;
                        OnUi(RebuildMenu);
                        return;
                    }
                }
            }
            catch (IOException)
            {
                // bootstrap still writing
            }

            Thread.Sleep(100);
        }
    }

    // MARK: - Python

    private void SupervisePython()
    {
        var bin = ServerBinary();
        if (bin is null)
        {
            OnUi(() =>
            {
                Alert(Tr("无法启动服务", "Could not start the server"), Tr("找不到 Amane.Server.exe。", "Amane.Server.exe is missing."));
                Native.PostMessage(_hwnd, Native.WmClose, 0, 0);
            });
            return;
        }

        while (true)
        {
            if (IsStopping)
            {
                return;
            }

            Process proc;
            try
            {
                proc = StartPython(bin);
            }
            catch (Exception ex)
            {
                OnUi(() =>
                {
                    Alert(Tr("无法启动服务", "Could not start the server"), ex.Message);
                    Native.PostMessage(_hwnd, Native.WmClose, 0, 0);
                });
                return;
            }

            lock (_gate)
            {
                _python = proc;
            }

            proc.WaitForExit();
            var code = proc.ExitCode;
            lock (_gate)
            {
                _python = null;
            }

            proc.Dispose();
            if (IsStopping)
            {
                return;
            }

            if (IsGraceful(code))
            {
                Native.PostMessage(_hwnd, Native.WmClose, 0, 0);
                return;
            }

            if (code == ExitRestart)
            {
                continue;
            }

            var deadline = DateTime.UtcNow + _restartDelay;
            while (!IsStopping && DateTime.UtcNow < deadline)
            {
                Thread.Sleep(50);
            }
        }
    }

    private Process StartPython(string bin)
    {
        var psi = new ProcessStartInfo
        {
            FileName = bin,
            WorkingDirectory = Path.GetDirectoryName(bin) ?? AppContext.BaseDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        psi.Environment["AMANE_UI_DISABLED"] = "1";
        foreach (var arg in Environment.GetCommandLineArgs().Skip(1))
        {
            psi.ArgumentList.Add(arg);
        }

        var proc = new Process { StartInfo = psi };
        if (!proc.Start())
        {
            throw new InvalidOperationException("CreateProcess failed");
        }

        if (_hJob != 0)
        {
            Native.AssignProcessToJobObject(_hJob, proc.Handle);
        }

        return proc;
    }

    private static bool IsGraceful(int code) =>
        code is 0 or 130 or 143 || unchecked((uint)code) == StatusControlCExit;

    private void StopPython(TimeSpan wait)
    {
        Process? proc;
        lock (_gate)
        {
            _stopping = true;
            proc = _python;
        }

        if (proc is null || proc.HasExited)
        {
            return;
        }

        if (!_uiOnly)
        {
            try
            {
                using var req = Authorized(HttpMethod.Post, "/api/system/restart");
                _http.Send(req, HttpCompletionOption.ResponseHeadersRead);
            }
            catch (Exception)
            {
                // Server not up yet — fall through to Kill.
            }
        }

        if (!proc.WaitForExit((int)wait.TotalMilliseconds))
        {
            try
            {
                proc.Kill(entireProcessTree: true);
            }
            catch (InvalidOperationException)
            {
                // already gone
            }

            proc.WaitForExit(2000);
        }
    }

    // MARK: - Window / tray

    private bool CreateUi()
    {
        _wndProc = WndProc;
        _hIcon = Native.LoadAppIcon(Environment.ProcessPath);
        var hInstance = Native.GetModuleHandle(null);
        var className = "AmaneDesktop";
        var wc = new Native.WndClassEx
        {
            cbSize = (uint)Marshal.SizeOf<Native.WndClassEx>(),
            style = Native.CsDblClks,
            lpfnWndProc = Marshal.GetFunctionPointerForDelegate(_wndProc),
            hInstance = hInstance,
            hIcon = _hIcon,
            hIconSm = _hIcon,
            lpszClassName = className,
        };
        if (Native.RegisterClassEx(ref wc) == 0)
        {
            return false;
        }

        _hwnd = Native.CreateWindowEx(
            Native.WsExToolwindow,
            className,
            "Amane",
            Native.WsPopup,
            0,
            0,
            0,
            0,
            0,
            0,
            hInstance,
            0
        );
        if (_hwnd == 0)
        {
            return false;
        }

        _taskbarCreated = Native.RegisterWindowMessage("TaskbarCreated");
        BuildMenu();
        AddTray();
        return true;
    }

    private static nint WndProc(nint hWnd, uint msg, nint wParam, nint lParam)
    {
        var app = _instance;
        if (app is null || (hWnd != app._hwnd && app._hwnd != 0))
        {
            return Native.DefWindowProc(hWnd, msg, wParam, lParam);
        }

        return app.Handle(hWnd, msg, wParam, lParam);
    }

    private nint Handle(nint hWnd, uint msg, nint wParam, nint lParam)
    {
        if (msg == _taskbarCreated && _taskbarCreated != 0)
        {
            _trayAdded = false;
            AddTray();
            return 0;
        }

        switch (msg)
        {
            case Native.WmTray:
                if (lParam is Native.WmRButtonUp or Native.WmContextMenu)
                {
                    ShowMenu();
                }
                else if (lParam == Native.WmLButtonDblClk)
                {
                    OpenWebUi();
                }

                return 0;
            case Native.WmTimer:
                if ((nuint)wParam == PollTimerId)
                {
                    QueuePoll();
                }

                return 0;
            case Native.WmDispatch:
                DrainUi();
                return 0;
            case Native.WmClose:
            case Native.WmEndSession:
                Quit();
                return 0;
            case Native.WmDestroy:
                Native.PostQuitMessage(0);
                return 0;
            default:
                return Native.DefWindowProc(hWnd, msg, wParam, lParam);
        }
    }

    private void AddTray()
    {
        _nid = new Native.NotifyIconData
        {
            cbSize = (uint)Marshal.SizeOf<Native.NotifyIconData>(),
            hWnd = _hwnd,
            uID = 1,
            uFlags = Native.NifMessage | Native.NifIcon | Native.NifTip,
            uCallbackMessage = Native.WmTray,
            hIcon = _hIcon,
            szTip = "Amane",
            szInfo = "",
            szInfoTitle = "",
        };
        if (Native.ShellNotifyIcon(_trayAdded ? Native.NimModify : Native.NimAdd, ref _nid))
        {
            _trayAdded = true;
        }
    }

    private void RemoveTray()
    {
        if (!_trayAdded)
        {
            return;
        }

        Native.ShellNotifyIcon(Native.NimDelete, ref _nid);
        _trayAdded = false;
    }

    private void BuildMenu()
    {
        if (_hMenu != 0)
        {
            Native.DestroyMenu(_hMenu);
        }

        _hMenu = Native.CreatePopupMenu();
        var statusFlags = Native.MfString | Native.MfGrayed | Native.MfDisabled;
        Native.AppendMenu(_hMenu, statusFlags, 0, Tr("连接中…", "Connecting…"));
        Native.AppendMenu(_hMenu, Native.MfSeparator, 0, null);
        Native.AppendMenu(_hMenu, Native.MfString, IdOpen, Tr("打开 Web UI", "Open Web UI"));
        Native.AppendMenu(
            _hMenu,
            Native.MfString | Native.MfGrayed | Native.MfDisabled,
            IdDataDir,
            Tr("打开数据目录", "Open Data Directory")
        );
        var copyFlags = Native.MfString;
        if (string.IsNullOrEmpty(_token))
        {
            copyFlags |= Native.MfGrayed | Native.MfDisabled;
        }

        Native.AppendMenu(_hMenu, copyFlags, IdCopy, Tr("复制 API Token", "Copy API Token"));
        Native.AppendMenu(_hMenu, Native.MfSeparator, 0, null);
        Native.AppendMenu(
            _hMenu,
            Native.MfString | Native.MfGrayed | Native.MfDisabled,
            IdUpdate,
            Tr("检查更新", "Check for Updates")
        );
        Native.AppendMenu(
            _hMenu,
            Native.MfString | Native.MfGrayed | Native.MfDisabled,
            IdRestart,
            Tr("重启服务器", "Restart Server")
        );
        Native.AppendMenu(_hMenu, Native.MfSeparator, 0, null);
        Native.AppendMenu(_hMenu, Native.MfString, IdQuit, Tr("退出 Amane", "Quit Amane"));
    }

    private void RebuildMenu()
    {
        BuildMenu();
        ApplyMenuState();
    }

    private void ApplyMenuState()
    {
        if (_hMenu == 0)
        {
            return;
        }

        var status = _connected
            ? Tr($"运行中 · v{_version}", $"Running · v{_version}")
            : Tr("未连接", "Disconnected");
        Native.ModifyMenu(
            _hMenu,
            0,
            Native.MfByPosition | Native.MfString | Native.MfGrayed | Native.MfDisabled,
            0,
            status
        );

        void Enable(uint id, bool on)
        {
            Native.EnableMenuItem(_hMenu, id, on ? 0 : Native.MfGrayed | Native.MfDisabled);
        }

        Enable(IdDataDir, _connected && _dataDir.Length > 0);
        Enable(IdCopy, _token.Length > 0);
        Enable(IdUpdate, _connected);
        Enable(IdRestart, _connected && _supervised && !_uiOnly);
        _nid.szTip = _connected
            ? Tr($"Amane 运行中 · v{_version}", $"Amane running · v{_version}")
            : Tr("Amane 服务未连接", "Amane not connected");
        if (_trayAdded)
        {
            Native.ShellNotifyIcon(Native.NimModify, ref _nid);
        }
    }

    private void ShowMenu()
    {
        Native.GetCursorPos(out var pt);
        // TrackPopupMenu takes the owner HWND's DPI. The message window lives at (0,0);
        // park it on the cursor's monitor so a high-DPI taskbar does not get a bitmap-stretched menu.
        Native.SetWindowPos(
            _hwnd,
            0,
            pt.X,
            pt.Y,
            0,
            0,
            Native.SwpNoSize | Native.SwpNoZOrder | Native.SwpNoActivate
        );
        Native.SetForegroundWindow(_hwnd);
        var cmd = Native.TrackPopupMenu(
            _hMenu,
            Native.TpmRightButton | Native.TpmReturnCmd,
            pt.X,
            pt.Y,
            0,
            _hwnd,
            0
        );
        Native.PostMessage(_hwnd, 0x0000, 0, 0);
        switch (cmd)
        {
            case IdOpen:
                OpenWebUi();
                break;
            case IdDataDir:
                OpenDataDirectory();
                break;
            case IdCopy:
                CopyToken();
                break;
            case IdUpdate:
                CheckUpdate();
                break;
            case IdRestart:
                RestartServer();
                break;
            case IdQuit:
                Native.PostMessage(_hwnd, Native.WmClose, 0, 0);
                break;
        }
    }

    // MARK: - Polling

    private void QueuePoll()
    {
        if (_pollInFlight)
        {
            return;
        }

        _pollInFlight = true;
        _ = Task.Run(async () =>
        {
            try
            {
                var snapshot = await PollDesktop();
                OnUi(() => Apply(snapshot));
            }
            finally
            {
                _pollInFlight = false;
            }
        });
    }

    private readonly record struct DesktopSnap(bool Connected, string Version, string DataDir, bool Supervised);

    private async Task<DesktopSnap> PollDesktop()
    {
        try
        {
            using var req = Authorized(HttpMethod.Get, "/api/system/desktop");
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            using var resp = await _http.SendAsync(req, cts.Token);
            if (!resp.IsSuccessStatusCode)
            {
                return default;
            }

            await using var stream = await resp.Content.ReadAsStreamAsync();
            using var doc = await JsonDocument.ParseAsync(stream);
            var root = doc.RootElement;
            var version = root.TryGetProperty("version", out var v) ? v.GetString() ?? "" : "";
            if (version.Length == 0)
            {
                return default;
            }

            var dataDir = root.TryGetProperty("data_dir", out var d) ? d.GetString() ?? "" : "";
            var supervised = root.TryGetProperty("supervised", out var s) && s.GetBoolean();
            return new DesktopSnap(true, version, dataDir, supervised);
        }
        catch (Exception)
        {
            return default;
        }
    }

    private void Apply(DesktopSnap snap)
    {
        _connected = snap.Connected;
        _version = snap.Version;
        _dataDir = snap.DataDir;
        _supervised = snap.Supervised;
        ApplyMenuState();
    }

    // MARK: - Actions

    private void OpenWebUi()
    {
        try
        {
            Process.Start(new ProcessStartInfo(_baseUrl) { UseShellExecute = true });
        }
        catch (Exception)
        {
            // ShellExecute failed; ignore.
        }
    }

    private void OpenDataDirectory()
    {
        if (_dataDir.Length == 0)
        {
            return;
        }

        try
        {
            Process.Start(new ProcessStartInfo(_dataDir) { UseShellExecute = true });
        }
        catch (Exception)
        {
            // ignore
        }
    }

    private void CopyToken()
    {
        if (_token.Length == 0)
        {
            return;
        }

        SetClipboardText(_token);
    }

    private void CheckUpdate()
    {
        _ = Task.Run(async () =>
        {
            try
            {
                using var req = Authorized(HttpMethod.Get, "/api/system/release");
                using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));
                using var resp = await _http.SendAsync(req, cts.Token);
                if (!resp.IsSuccessStatusCode)
                {
                    OnUi(() =>
                        Alert(
                            Tr("检查更新失败", "Update check failed"),
                            Tr("暂时无法联系 GitHub，请稍后重试。", "Could not reach GitHub. Try again later.")
                        )
                    );
                    return;
                }

                await using var stream = await resp.Content.ReadAsStreamAsync(cts.Token);
                using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: cts.Token);
                var root = doc.RootElement;
                var newer = root.TryGetProperty("newer", out var n) && n.GetBoolean();
                var html = root.TryGetProperty("html_url", out var h) ? h.GetString() : null;
                OnUi(() =>
                {
                    if (newer && !string.IsNullOrEmpty(html))
                    {
                        try
                        {
                            Process.Start(new ProcessStartInfo(html) { UseShellExecute = true });
                        }
                        catch (Exception)
                        {
                            // ignore
                        }
                    }
                    else
                    {
                        Alert(Tr("已是最新", "Up to date"), Tr("当前已是最新版本。", "You are running the latest version."));
                    }
                });
            }
            catch (Exception)
            {
                OnUi(() =>
                    Alert(
                        Tr("检查更新失败", "Update check failed"),
                        Tr("暂时无法联系 GitHub，请稍后重试。", "Could not reach GitHub. Try again later.")
                    )
                );
            }
        });
    }

    private void RestartServer()
    {
        _ = Task.Run(async () =>
        {
            try
            {
                using var req = Authorized(HttpMethod.Post, "/api/system/restart");
                using var resp = await _http.SendAsync(req);
                if (!resp.IsSuccessStatusCode)
                {
                    OnUi(() =>
                        Alert(Tr("重启失败", "Restart failed"), Tr("无法请求重启。", "Could not request a restart."))
                    );
                }
            }
            catch (Exception)
            {
                OnUi(() =>
                    Alert(Tr("重启失败", "Restart failed"), Tr("无法请求重启。", "Could not request a restart."))
                );
            }
        });
    }

    private void Quit()
    {
        if (_unwinding)
        {
            return;
        }

        _unwinding = true;
        Native.KillTimer(_hwnd, PollTimerId);
        StopPython(TimeSpan.FromSeconds(5));
        RemoveTray();
        Native.DestroyWindow(_hwnd);
    }

    private void Teardown()
    {
        if (_hMenu != 0)
        {
            Native.DestroyMenu(_hMenu);
            _hMenu = 0;
        }

        if (_hIcon != 0)
        {
            Native.DestroyIcon(_hIcon);
            _hIcon = 0;
        }

        if (_hJob != 0)
        {
            Native.CloseHandle(_hJob);
            _hJob = 0;
        }

        _http.Dispose();
        GC.KeepAlive(_wndProc);
    }

    // MARK: - HTTP / UI helpers

    private HttpRequestMessage Authorized(HttpMethod method, string path)
    {
        var req = new HttpRequestMessage(method, _baseUrl + path);
        if (_token.Length > 0)
        {
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _token);
        }

        return req;
    }

    private void OnUi(Action action)
    {
        _ui.Enqueue(action);
        if (_hwnd != 0)
        {
            Native.PostMessage(_hwnd, Native.WmDispatch, 0, 0);
        }
    }

    private void DrainUi()
    {
        while (_ui.TryDequeue(out var action))
        {
            action();
        }
    }

    private void Alert(string title, string message)
    {
        Native.MessageBox(_hwnd, message, title, Native.MbOk | Native.MbIconInformation);
    }

    private static void SetClipboardText(string text)
    {
        if (!Native.OpenClipboard(0))
        {
            return;
        }

        try
        {
            Native.EmptyClipboard();
            var bytes = Encoding.Unicode.GetBytes(text + "\0");
            var hMem = Native.GlobalAlloc(Native.GmemMoveable, (nuint)bytes.Length);
            if (hMem == 0)
            {
                return;
            }

            var ptr = Native.GlobalLock(hMem);
            Marshal.Copy(bytes, 0, ptr, bytes.Length);
            Native.GlobalUnlock(hMem);
            if (Native.SetClipboardData(Native.CfUnicodeText, hMem) == 0)
            {
                Native.GlobalFree(hMem);
            }
        }
        finally
        {
            Native.CloseClipboard();
        }
    }

    private static nint CreateKillOnCloseJob()
    {
        var job = Native.CreateJobObject(0, null);
        if (job == 0)
        {
            return 0;
        }

        var info = new Native.JobObjectExtendedLimitInformation
        {
            BasicLimitInformation = new Native.JobObjectBasicLimitInformation
            {
                LimitFlags = Native.JobObjectLimitKillOnJobClose,
            },
        };
        if (
            !Native.SetInformationJobObject(
                job,
                Native.JobObjectInfoExtendedLimit,
                ref info,
                (uint)Marshal.SizeOf<Native.JobObjectExtendedLimitInformation>()
            )
        )
        {
            Native.CloseHandle(job);
            return 0;
        }

        return job;
    }

    private bool IsStopping
    {
        get
        {
            lock (_gate)
            {
                return _stopping;
            }
        }
    }

    private static string? Env(string key) => Environment.GetEnvironmentVariable(key);

    private static void SetDefault(string key, string value)
    {
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable(key)))
        {
            Environment.SetEnvironmentVariable(key, value);
        }
    }

    private static string Tr(string zh, string en) => Zh ? zh : en;
}
