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
  /** 清除本机保存的登录态并回到服务器设置页; 服务器地址列表保留. */
  signOut(): void;
  /** 壳的版本号, 与 APK 的 `versionName` 一致. */
  shellVersion(): string;
  /** 系统 WebView 的版本号 (如 `126.0.6478.122`); 取不到时为空串. */
  webViewVersion(): string;
}

declare global {
  interface Window {
    amaneshell?: AmaneShellBridge;
  }
}

/**
 * 壳渲染所需的最低 WebView 主版本.
 *
 * 取实测最低值 (实测最低内核), 与 `vite.config.ts` 的
 * 运行期下限同一条线; 更低的内核达不到这个下限, 须由用户更新「Android System WebView」.
 */
export const MIN_WEBVIEW_MAJOR = 99;

/** Android System WebView 的应用商店页, 供版本过低时跳转. */
export const WEBVIEW_STORE_URL =
  "https://play.google.com/store/apps/details?id=com.google.android.webview";

const SHELL_MARKER = /\bAmaneShell\/(\S+)/;
const CHROME_VERSION = /\bChrome\/([0-9.]+)/;

export interface ShellEnvironment {
  /** 壳的版本号, 取自 UA 标记. */
  version: string;
  /** 系统 WebView 的版本串; 桥不可用时退回 UA 里的 Chrome 版本. */
  webViewVersion: string;
  /** 系统 WebView 的主版本; 解析不出时为 null. */
  webViewMajor: number | null;
  /** WebView 低于前端下限: 页面可能渲染残缺. */
  webViewOutdated: boolean;
  /** JS 桥是否可用: 服务器切换与退出登录依赖它. */
  bridgeAvailable: boolean;
}

/** 壳内的运行环境; 不在壳内 (普通浏览器 / Docker) 时返回 null. */
export function shellEnvironment(): ShellEnvironment | null {
  const marker = SHELL_MARKER.exec(navigator.userAgent);
  if (!marker) return null;

  const bridge = window.amaneshell;
  let bridged = "";
  let bridgeAvailable = false;
  if (bridge) {
    // 读版本失败不应影响"是否在壳内"的判断, 因此单独兜住.
    try {
      bridged = bridge.webViewVersion();
      bridgeAvailable = true;
    } catch {
      bridgeAvailable = false;
    }
  }

  const webViewVersion = bridged || CHROME_VERSION.exec(navigator.userAgent)?.[1] || "";
  const major = Number.parseInt(webViewVersion, 10);
  const webViewMajor = Number.isNaN(major) ? null : major;
  return {
    version: marker[1],
    webViewVersion,
    webViewMajor,
    webViewOutdated: webViewMajor !== null && webViewMajor < MIN_WEBVIEW_MAJOR,
    bridgeAvailable,
  };
}

/** 打开壳的服务器设置页; 桥不可用时返回 false, 由调用方提示而不是静默失败. */
export function shellSwitchServer(): boolean {
  if (!window.amaneshell) return false;
  window.amaneshell.switchServer();
  return true;
}

/** 退出登录 (清本机登录态并回服务器页); 桥不可用时返回 false. */
export function shellSignOut(): boolean {
  if (!window.amaneshell) return false;
  window.amaneshell.signOut();
  return true;
}
