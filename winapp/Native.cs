// Win32 surface used by the merged supervisor + tray process.
// Keep this file mechanical: signatures, structs, constants. Policy lives in Program.cs.

using System.Runtime.InteropServices;

namespace Amane;

internal static class Native
{
    internal const int WmClose = 0x0010;
    internal const int WmDestroy = 0x0002;
    internal const int WmCommand = 0x0111;
    internal const int WmTimer = 0x0113;
    internal const int WmRButtonUp = 0x0205;
    internal const int WmLButtonDblClk = 0x0203;
    internal const int WmContextMenu = 0x007B;
    internal const int WmApp = 0x8000;
    internal const int WmTray = WmApp + 1;
    internal const int WmDispatch = WmApp + 2;
    internal const int WmEndSession = 0x0016;
    internal const int WmQueryEndSession = 0x0011;

    internal const uint NimAdd = 0;
    internal const uint NimModify = 1;
    internal const uint NimDelete = 2;
    internal const uint NifMessage = 0x0001;
    internal const uint NifIcon = 0x0002;
    internal const uint NifTip = 0x0004;

    internal const uint MfString = 0;
    internal const uint MfSeparator = 0x0800;
    internal const uint MfGrayed = 0x0001;
    internal const uint MfDisabled = 0x0002;
    internal const uint MfByPosition = 0x0400;
    internal const uint TpmRightButton = 0x0002;
    internal const uint TpmReturnCmd = 0x0100;
    internal const uint SwpNoSize = 0x0001;
    internal const uint SwpNoZOrder = 0x0004;
    internal const uint SwpNoActivate = 0x0010;
    internal const nint DpiAwarenessContextPerMonitorAwareV2 = -4;

    internal const uint WsPopup = 0x80000000;
    internal const uint WsExToolwindow = 0x00000080;
    internal const uint CsDblClks = 0x0008;

    internal const uint JobObjectInfoExtendedLimit = 9;
    internal const uint JobObjectLimitKillOnJobClose = 0x2000;
    internal const uint JobObjectLimitBreakawayOk = 0x800;

    internal const uint GmemMoveable = 0x0002;
    internal const uint CfUnicodeText = 13;
    internal const uint MbOk = 0;
    internal const uint MbIconInformation = 0x40;

    internal const int IconSize = 32;

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    internal delegate nint WndProc(nint hWnd, uint msg, nint wParam, nint lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    internal static extern ushort RegisterClassEx(ref WndClassEx lpwcx);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    internal static extern nint CreateWindowEx(
        uint dwExStyle,
        string lpClassName,
        string lpWindowName,
        uint dwStyle,
        int x,
        int y,
        int nWidth,
        int nHeight,
        nint hWndParent,
        nint hMenu,
        nint hInstance,
        nint lpParam);

    [DllImport("user32.dll")]
    internal static extern nint DefWindowProc(nint hWnd, uint msg, nint wParam, nint lParam);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetMessage(out Msg lpMsg, nint hWnd, uint wMsgFilterMin, uint wMsgFilterMax);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool TranslateMessage(in Msg lpMsg);

    [DllImport("user32.dll")]
    internal static extern nint DispatchMessage(in Msg lpMsg);

    [DllImport("user32.dll")]
    internal static extern void PostQuitMessage(int nExitCode);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool PostMessage(nint hWnd, uint msg, nint wParam, nint lParam);

    [DllImport("user32.dll")]
    internal static extern nuint SetTimer(nint hWnd, nuint nIdEvent, uint uElapse, nint lpTimerFunc);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool KillTimer(nint hWnd, nuint uIdEvent);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool DestroyWindow(nint hWnd);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetForegroundWindow(nint hWnd);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetCursorPos(out Point lpPoint);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    internal static extern uint RegisterWindowMessage(string lpString);

    [DllImport("user32.dll")]
    internal static extern nint CreatePopupMenu();

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool AppendMenu(nint hMenu, uint uFlags, nuint uIdNewItem, string? lpNewItem);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool ModifyMenu(
        nint hMenu,
        uint uPosition,
        uint uFlags,
        nuint uIdNewItem,
        string lpNewItem);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool EnableMenuItem(nint hMenu, uint uIdEnableItem, uint uEnable);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool DestroyMenu(nint hMenu);

    [DllImport("user32.dll")]
    internal static extern uint TrackPopupMenu(
        nint hMenu,
        uint uFlags,
        int x,
        int y,
        int nReserved,
        nint hWnd,
        nint prcRect);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    internal static extern int MessageBox(nint hWnd, string lpText, string lpCaption, uint uType);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool DestroyIcon(nint hIcon);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetWindowPos(
        nint hWnd,
        nint hWndInsertAfter,
        int x,
        int y,
        int cx,
        int cy,
        uint uFlags
    );

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetProcessDpiAwarenessContext(nint value);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool OpenClipboard(nint hWndNewOwner);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool EmptyClipboard();

    [DllImport("user32.dll")]
    internal static extern nint SetClipboardData(uint uFormat, nint hMem);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool CloseClipboard();

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, EntryPoint = "Shell_NotifyIconW")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool ShellNotifyIcon(uint dwMessage, ref NotifyIconData lpData);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    internal static extern nint GetModuleHandle(string? lpModuleName);

    [DllImport("kernel32.dll")]
    internal static extern ushort GetUserDefaultUILanguage();

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    internal static extern nint CreateJobObject(nint lpJobAttributes, string? lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetInformationJobObject(
        nint hJob,
        uint jobObjectInfoClass,
        ref JobObjectExtendedLimitInformation lpJobObjectInfo,
        uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool AssignProcessToJobObject(nint hJob, nint hProcess);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool CloseHandle(nint hObject);

    [DllImport("kernel32.dll")]
    internal static extern nint GlobalAlloc(uint uFlags, nuint dwBytes);

    [DllImport("kernel32.dll")]
    internal static extern nint GlobalLock(nint hMem);

    [DllImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GlobalUnlock(nint hMem);

    [DllImport("kernel32.dll")]
    internal static extern nint GlobalFree(nint hMem);

    [DllImport("gdi32.dll")]
    internal static extern nint CreateDIBSection(
        nint hdc,
        in BitmapInfo pbmi,
        uint usage,
        out nint ppvBits,
        nint hSection,
        uint offset);

    [DllImport("gdi32.dll")]
    internal static extern nint CreateBitmap(int nWidth, int nHeight, uint cPlanes, uint cBitsPerPel, byte[]? lpvBits);

    [DllImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool DeleteObject(nint ho);

    [DllImport("user32.dll")]
    internal static extern nint CreateIconIndirect(in IconInfo piconinfo);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    internal static extern uint ExtractIconEx(
        string lpszFile,
        int nIconIndex,
        out nint phiconLarge,
        out nint phiconSmall,
        uint nIcons);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct WndClassEx
    {
        public uint cbSize;
        public uint style;
        public nint lpfnWndProc;
        public int cbClsExtra;
        public int cbWndExtra;
        public nint hInstance;
        public nint hIcon;
        public nint hCursor;
        public nint hbrBackground;
        public nint lpszMenuName;
        [MarshalAs(UnmanagedType.LPWStr)]
        public string? lpszClassName;
        public nint hIconSm;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct Msg
    {
        public nint hwnd;
        public uint message;
        public nint wParam;
        public nint lParam;
        public uint time;
        public Point pt;
        public uint lPrivate;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct Point
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct NotifyIconData
    {
        public uint cbSize;
        public nint hWnd;
        public uint uID;
        public uint uFlags;
        public uint uCallbackMessage;
        public nint hIcon;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string szTip;

        public uint dwState;
        public uint dwStateMask;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)]
        public string szInfo;

        public uint uVersion;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)]
        public string szInfoTitle;

        public uint dwInfoFlags;
        public Guid guidItem;
        public nint hBalloonIcon;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct JobObjectBasicLimitInformation
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public nuint MinimumWorkingSetSize;
        public nuint MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public nuint Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct IoCounters
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct JobObjectExtendedLimitInformation
    {
        public JobObjectBasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public nuint ProcessMemoryLimit;
        public nuint JobMemoryLimit;
        public nuint PeakProcessMemoryUsed;
        public nuint PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct BitmapInfoHeader
    {
        public uint biSize;
        public int biWidth;
        public int biHeight;
        public ushort biPlanes;
        public ushort biBitCount;
        public uint biCompression;
        public uint biSizeImage;
        public int biXPelsPerMeter;
        public int biYPelsPerMeter;
        public uint biClrUsed;
        public uint biClrImportant;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct BitmapInfo
    {
        public BitmapInfoHeader bmiHeader;
        public uint bmiColors;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct IconInfo
    {
        [MarshalAs(UnmanagedType.Bool)]
        public bool fIcon;

        public int xHotspot;
        public int yHotspot;
        public nint hbmMask;
        public nint hbmColor;
    }

    internal static bool IsChineseUi()
    {
        return (GetUserDefaultUILanguage() & 0x3FF) == 0x04;
    }

    internal static nint LoadAppIcon(string? exePath)
    {
        if (!string.IsNullOrEmpty(exePath))
        {
            nint large = 0;
            nint small = 0;
            if (ExtractIconEx(exePath, 0, out large, out small, 1) > 0 && large != 0)
            {
                if (small != 0)
                {
                    DestroyIcon(small);
                }

                return large;
            }

            if (small != 0)
            {
                DestroyIcon(small);
            }
        }

        return CreateLetterAIcon();
    }

    internal static nint CreateLetterAIcon()
    {
        const int size = IconSize;
        var info = new BitmapInfo
        {
            bmiHeader = new BitmapInfoHeader
            {
                biSize = (uint)Marshal.SizeOf<BitmapInfoHeader>(),
                biWidth = size,
                biHeight = -size,
                biPlanes = 1,
                biBitCount = 32,
            },
        };
        var dib = CreateDIBSection(0, in info, 0, out var bits, 0, 0);
        if (dib == 0 || bits == 0)
        {
            return 0;
        }

        var pixels = new byte[size * size * 4];
        FillBadge(pixels, size);
        Marshal.Copy(pixels, 0, bits, pixels.Length);

        var mask = CreateBitmap(size, size, 1, 1, new byte[((size * size) + 7) / 8]);
        var iconInfo = new IconInfo
        {
            fIcon = true,
            xHotspot = 0,
            yHotspot = 0,
            hbmMask = mask,
            hbmColor = dib,
        };
        var icon = CreateIconIndirect(in iconInfo);
        DeleteObject(dib);
        if (mask != 0)
        {
            DeleteObject(mask);
        }

        return icon;
    }

    private static void FillBadge(byte[] pixels, int size)
    {
        // Same badge as assets/logo.svg: vertical gradient #3385ff → #0f4fc4,
        // rx=8/32, 18-unit glyph grid mapped to 6..26 / 5..25, solid A with
        // play-triangle counter. Corners drawn sharp (SVG 的圆角描边在此省略).
        var radius = Math.Max(1, size / 4);
        var sx0 = size * 6f / 32f;
        var sy0 = size * 5f / 32f;
        var span = size * 20f / 32f;
        float SX(float v) => sx0 + (v / 18f * span);
        float SY(float v) => sy0 + (v / 18f * span);

        // 18 格网格 (y 向下) → 屏幕坐标 (y 向下).
        // 外轮廓 A: 顶点 (9,0), 脚 (0,18)/(18,18); 播放字腔: (7,8.5),(7,14.5),(12.6,11.5).
        var ax = SX(9f);
        var ay = SY(0f);
        var lx = SX(0f);
        var ly = SY(18f);
        var rxx = SX(18f);
        var c1x = SX(7f);
        var c1y = SY(8.5f);
        var c2x = SX(7f);
        var c2y = SY(14.5f);
        var c3x = SX(12.6f);
        var c3y = SY(11.5f);

        for (var y = 0; y < size; y++)
        {
            var t = size > 1 ? y / (float)(size - 1) : 0f;
            var br = (byte)(51 + ((15 - 51) * t));
            var bg = (byte)(133 + ((79 - 133) * t));
            var bb = (byte)(255 + ((196 - 255) * t));
            for (var x = 0; x < size; x++)
            {
                if (!InsideRoundRect(x, y, size, radius))
                {
                    continue;
                }

                if (InsideTriangle(x, y, ax, ay, lx, ly, rxx, ly)
                    && !InsideTriangle(x, y, c1x, c1y, c2x, c2y, c3x, c3y))
                {
                    Put(pixels, size, x, y, 255, 255, 255, 255);
                }
                else
                {
                    Put(pixels, size, x, y, br, bg, bb, 255);
                }
            }
        }
    }

    private static bool InsideTriangle(
        float px,
        float py,
        float ax,
        float ay,
        float bx,
        float by,
        float cx,
        float cy
    )
    {
        var d1 = ((px - bx) * (ay - by)) - ((ax - bx) * (py - by));
        var d2 = ((px - cx) * (by - cy)) - ((bx - cx) * (py - cy));
        var d3 = ((px - ax) * (cy - ay)) - ((cx - ax) * (py - ay));
        var hasNeg = d1 < 0 || d2 < 0 || d3 < 0;
        var hasPos = d1 > 0 || d2 > 0 || d3 > 0;
        return !(hasNeg && hasPos);
    }

    private static bool InsideRoundRect(int x, int y, int size, int radius)
    {
        var max = size - 1;
        if (x >= radius && x <= max - radius)
        {
            return true;
        }

        if (y >= radius && y <= max - radius)
        {
            return true;
        }

        static bool InCorner(int cx, int cy, int px, int py, int r) =>
            ((px - cx) * (px - cx)) + ((py - cy) * (py - cy)) <= r * r;

        if (x < radius && y < radius)
        {
            return InCorner(radius, radius, x, y, radius);
        }

        if (x > max - radius && y < radius)
        {
            return InCorner(max - radius, radius, x, y, radius);
        }

        if (x < radius && y > max - radius)
        {
            return InCorner(radius, max - radius, x, y, radius);
        }

        if (x > max - radius && y > max - radius)
        {
            return InCorner(max - radius, max - radius, x, y, radius);
        }

        return true;
    }

    private static void Put(byte[] pixels, int size, int x, int y, byte r, byte g, byte b, byte a)
    {
        if ((uint)x >= (uint)size || (uint)y >= (uint)size)
        {
            return;
        }

        var i = ((y * size) + x) * 4;
        pixels[i] = b;
        pixels[i + 1] = g;
        pixels[i + 2] = r;
        pixels[i + 3] = a;
    }
}
