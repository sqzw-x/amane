import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import legacy from "@vitejs/plugin-legacy";
import { tanstackRouter } from "@tanstack/router-vite-plugin";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

/**
 * 运行期兼容下限, 面向版本落后于构建默认目标的内核 (壳的 WebView 由设备提供, 版本不受本仓库控制).
 *
 * `build.target` 只降语法: 内建方法 (`Array.prototype.toSorted` 等) 不会被降级, 缺失时只能由 polyfill 提供.
 * `modernTargets` 同时充当语法目标与 `@babel/preset-env` 的收集目标, polyfill 按 bundle 的实际使用自动挑,
 * 因此新增依赖或新增调用不需要维护清单.
 *
 * Chromium 下限与 `web/src/lib/shell.ts` 的 `MIN_CHROMIUM_MAJOR` 必须同源. 该下限只保证 JS 不崩: 样式依赖的
 * 新 CSS 特性无法由 core-js 补齐, 低于下限的内核须由用户更新系统 WebView (依据见 `docs/dev/android.md`).
 */
const MODERN_TARGETS = [
  "chrome >= 99",
  "chromeAndroid >= 99",
  "edge >= 99",
  "firefox >= 115",
  "safari >= 16.4",
  "ios_saf >= 16.4",
];

/**
 * 把第三方代码里的 `dvh` 换成 `--amane-vh` (定义见 `src/global.css`).
 *
 * 需要它的是 Mantine: 弹窗的 `yOffset` 默认值是 `5dvh`, 由 JS 写进内联样式, 只改 CSS 覆盖不到. 自己写的代码
 * 一律直接用 `var(--amane-vh)`, 不依赖这一步; 兜底的必要性见 `docs/dev/frontend.md`.
 */
function viewportUnitFallback(): Plugin {
  const SUPPORTS = /@supports[^{]*\{/g;
  // 前面必须有数字才是长度单位: hls.js 的编解码器名 (`dvh1` / `dvhe`) 与 CSS 单位清单里的裸 `dvh` 都不匹配.
  const DVH_LENGTH = /([\d.]+)dvh\b/g;
  return {
    name: "amane-viewport-unit-fallback",
    enforce: "post",
    transform(code, id) {
      if (!id.includes("node_modules") || id.endsWith(".css")) return null;
      const next = code.replace(DVH_LENGTH, (_match, size: string) => `${size}vh`);
      return next === code ? null : { code: next, map: null };
    },
    generateBundle(_options, bundle) {
      for (const file of Object.values(bundle)) {
        if (file.type !== "asset" || !file.fileName.endsWith(".css")) continue;
        const css = String(file.source);
        // 构建期替换分不清 `@supports` 预查与普通声明, 预查段落必须先保护起来, 否则特性查询本身失效.
        const shielded = css.replace(SUPPORTS, (at) => at.replaceAll("dvh", "\u0000dvh\u0000"));
        file.source = shielded
          .replace(DVH_LENGTH, (_match, size: string) =>
            size === "100" ? "var(--amane-vh)" : `calc(var(--amane-vh) * ${size} / 100)`,
          )
          .replaceAll("\u0000dvh\u0000", "dvh");
      }
    },
  };
}

export default defineConfig({
  plugins: [
    tanstackRouter({ autoCodeSplitting: true }),
    react(),
    // renderLegacyChunks: 下限内核都支持 ESM, 不需要 SystemJS 包, 只要语法降级与 polyfill.
    legacy({
      modernTargets: MODERN_TARGETS,
      modernPolyfills: true,
      renderLegacyChunks: false,
    }),
    viewportUnitFallback(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(rootDir, "./src"),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_URL || "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
