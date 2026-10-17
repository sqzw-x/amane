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
  /** 系统媒体音量, 0..1; 旧版壳没有这一组方法. */
  mediaVolume?(): number;
  setMediaVolume?(value: number): void;
  /** 系统媒体音量的一档大小; 与上一组一并出现, 但另行判空 —— 已装出去的壳可能只有上一组. */
  mediaVolumeStep?(): number;
  /** 窗口亮度, 0..1. */
  screenBrightness?(): number;
  setScreenBrightness?(value: number): void;
  /** 交还系统亮度. */
  clearScreenBrightness?(): void;
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

/**
 * 壳内可直接写入的系统媒体音量与屏幕亮度.
 *
 * 页面没有设置这两个系统值的 API, 因此只在壳内存在; 取值与写入都经桥, 桥只能同步返回, 因此壳侧的读取
 * 必须落在 JavaBridge 线程可读的字段上 (见 `BrowserActivity.screenBrightness`).
 */
export interface ShellSystemLevels {
  /** 系统媒体音量, 0..1. */
  volume(): number;
  setVolume(value: number): void;
  /**
   * 系统媒体音量的一档大小 (1 / 档位数); 旧壳不提供时为 0, 调用方按值去重即可.
   *
   * 音量按档位生效: 一档之内写多少次都是同一个结果, 而每次写入都要跨进程进 AudioService,
   * 因此调用方按它取整, 只在跨档时写.
   */
  volumeStep: number;
  /** 窗口亮度, 0..1. */
  brightness(): number;
  setBrightness(value: number): void;
  /** 交还系统亮度. */
  clearBrightness(): void;
}

/**
 * 取壳的系统音量与亮度接口; 桥不可用或壳版本早于这一组方法时返回 null.
 *
 * 返回 null 时调用方退回元素音量与画面叠加层: 浏览器内没有别的手段, 旧壳内则是降级.
 */
export function shellSystemLevels(): ShellSystemLevels | null {
  const bridge = window.amaneshell;
  if (bridge == null) return null;
  const {
    mediaVolume,
    setMediaVolume,
    screenBrightness,
    setScreenBrightness,
    clearScreenBrightness,
  } = bridge;
  if (
    mediaVolume == null ||
    setMediaVolume == null ||
    screenBrightness == null ||
    setScreenBrightness == null ||
    clearScreenBrightness == null
  ) {
    return null;
  }
  // 以桥为接收者调用, 不假设 Java 桥的方法可以拆出来单调用.
  // 档位是可选方法: 已装出去的壳没有它, 缺了只是不去重, 不影响音量本身.
  const { mediaVolumeStep } = bridge;
  return {
    volume: () => mediaVolume.call(bridge),
    setVolume: (value) => setMediaVolume.call(bridge, value),
    volumeStep: mediaVolumeStep == null ? 0 : mediaVolumeStep.call(bridge),
    brightness: () => screenBrightness.call(bridge),
    setBrightness: (value) => setScreenBrightness.call(bridge, value),
    clearBrightness: () => clearScreenBrightness.call(bridge),
  };
}
