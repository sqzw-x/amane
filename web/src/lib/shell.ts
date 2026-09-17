/**
 * Android 壳的运行环境.
 *
 * 判据是壳写在 User-Agent 上的标记 `AmaneShell/<版本>` (`WebSettings.userAgentString`, 每个请求都带),
 * **不是** JS 桥对象: 桥只承载动作, 缺了它不该让入口整块消失 — 那正是"有时显示有时不显示"的来源.
 * 桌面浏览器与 Docker 部署没有该标记, 相关入口整块不渲染.
 *
 * 鉴权失效时壳内不显示页面的登录门: token 由壳的服务器页校验并换取 cookie (见 `docs/dev/android.md`),
 * 因此未认证时跳回该页, 登录门只作为用户返回后的兜底.
 *
 * 安全边界: `addJavascriptInterface` 对 WebView 加载的文档全部可见, 因此壳只让服务端自身
 * origin 留在 WebView 内, 站外链接交给系统浏览器 (见 `androidapp/.../BrowserActivity.kt`).
 */

interface AmaneShellBridge {
  /** 打开壳的服务器设置页. */
  switchServer(): void;
  /** 壳的版本号, 与 APK 的 `versionName` 一致. */
  shellVersion(): string;
  /** 系统 WebView 的提供方与包版本, 厂商包版本; 取不到时为空串. */
  webViewPackage(): string;
}

declare global {
  interface Window {
    amaneshell?: AmaneShellBridge;
  }
}

/**
 * 壳渲染所需的最低 WebView 主版本, 按 **Chromium** 计.
 *
 * 取实测最低值 (UA 里是 `Chrome/99.0.4844.88`), 与 `vite.config.ts`
 * 的运行期下限同一条线; 更低的内核须由用户更新系统 WebView.
 */
export const MIN_CHROMIUM_MAJOR = 99;

/** 只在提供方是 Google 发行的包时给出商店链接: 厂商自带的 WebView 在 Play 上没有条目. */
export function webViewStoreUrl(packageName: string): string | null {
  const google =
    packageName.startsWith("com.google.android.webview") || packageName === "com.android.chrome";
  return google ? `https://play.google.com/store/apps/details?id=${packageName}` : null;
}

const SHELL_MARKER = /\bAmaneShell\/(\S+)/;
const CHROME_VERSION = /\bChrome\/([0-9.]+)/;

export interface ShellEnvironment {
  /** 壳的版本号, 取自 UA 标记. */
  version: string;
  /** 系统 WebView 的提供方与包版本, 厂商包版本; 桥不可用时为空串. */
  packageLabel: string;
  /** 渲染内核 (Chromium) 版本, 取自 UA 的 `Chrome/<版本>`; 解析不出时为空串. */
  chromiumVersion: string;
  /** 渲染内核主版本; 解析不出时为 null. */
  chromiumMajor: number | null;
  /** 渲染内核低于前端下限: 页面可能渲染残缺. */
  chromiumOutdated: boolean;
  /** JS 桥是否可用: 服务器切换依赖它. */
  bridgeAvailable: boolean;
}

/**
 * 壳内的运行环境; 不在壳内 (普通浏览器 / Docker) 时返回 null.
 *
 * 内核版本只认 UA: `WebViewCompat.getCurrentWebViewPackage` 的版本号是**厂商包版本** (
 * 就是 `14`), 与 Chromium 版本没有对应关系, 拿它比较下限会误报.
 */
export function shellEnvironment(): ShellEnvironment | null {
  const marker = SHELL_MARKER.exec(navigator.userAgent);
  if (!marker) return null;

  let packageLabel = "";
  let bridgeAvailable = false;
  const bridge = window.amaneshell;
  if (bridge) {
    // 读包信息失败不应影响"是否在壳内"的判断, 因此单独兜住.
    try {
      packageLabel = bridge.webViewPackage();
      bridgeAvailable = true;
    } catch {
      bridgeAvailable = false;
    }
  }

  const chromiumVersion = CHROME_VERSION.exec(navigator.userAgent)?.[1] ?? "";
  const major = Number.parseInt(chromiumVersion, 10);
  const chromiumMajor = Number.isNaN(major) ? null : major;
  return {
    version: marker[1],
    packageLabel,
    chromiumVersion,
    chromiumMajor,
    chromiumOutdated: chromiumMajor !== null && chromiumMajor < MIN_CHROMIUM_MAJOR,
    bridgeAvailable,
  };
}

/** 打开壳的服务器设置页; 桥不可用时返回 false, 由调用方提示而不是静默失败. */
export function shellSwitchServer(): boolean {
  if (!window.amaneshell) return false;
  window.amaneshell.switchServer();
  return true;
}
