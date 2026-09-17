/**
 * Android 壳的运行环境.
 *
 * 是否在壳内只依据 User-Agent 上的 `AmaneShell/<版本>` 标记 (`WebSettings.userAgentString`, 每个请求都带),
 * 不依据 JS 桥: 桥只承载动作, 桥不可用不代表不在壳内, 入口不该整块消失. 桌面浏览器与 Docker 部署没有该标记,
 * 相关入口整块不渲染.
 *
 * 鉴权与 origin 契约见 `docs/dev/android.md`: 壳内不显示页面的登录门, 未认证时跳回壳的服务器页一次.
 */

interface AmaneShellBridge {
  /** 打开壳的服务器设置页. */
  switchServer(): void;
  /** 系统 WebView 的提供方与包版本; 取不到时为空串. */
  webViewPackage(): string;
  /** 报告触点处是否还有可以向上滚的内容, 供壳决定下拉刷新是否接管手势; 旧版壳没有这个方法. */
  setPageScrollableUp?(scrollableUp: boolean): void;
}

declare global {
  interface Window {
    amaneshell?: AmaneShellBridge;
  }
}

/**
 * 壳渲染所需的最低 WebView 主版本, 按 **Chromium** 计; 与 `vite.config.ts` 的 `MODERN_TARGETS` 必须同源.
 * 下限的依据与更新方式见 `docs/dev/android.md`.
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
  /** APP 版本号, 取自 UA 标记. */
  version: string;
  /** 系统 WebView 的提供方与包版本; 桥不可用时为空串. */
  packageLabel: string;
  /** 渲染内核 (Chromium) 版本; 解析不出时为空串. */
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
 * 内核版本取 UA 的 `Chrome/<版本>` 而非厂商包版本, 理由见 `docs/dev/android.md`.
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
