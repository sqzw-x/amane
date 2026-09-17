import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import legacy from "@vitejs/plugin-legacy";
import { tanstackRouter } from "@tanstack/router-vite-plugin";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

/**
 * 运行期兼容下限, 面向版本落后于构建默认目标的内核 (Android 壳的 WebView 由设备提供, 版本不受本仓库控制).
 *
 * `build.target` 只降语法: 内建方法 (`Array.prototype.toSorted` 等) 不会被降级, 缺失时只能由 polyfill 提供.
 * `modernTargets` 同时充当语法目标与 `@babel/preset-env` 的收集目标, polyfill 按 bundle 的实际使用自动挑,
 * 因此新增依赖或新增调用不需要维护清单.
 *
 * 下限取 Chromium 108 / Safari 16.4 — 与样式表实际依赖的特性一致 (`dvh` 108, `:has()` 与 `@container` 105,
 * `color-mix()` 111). CSS 不能由 core-js 补, 把下限声明得低于样式表的真实下限, 只会把"明确报错"换成
 * "能渲染但残缺".
 */
const MODERN_TARGETS = [
  "chrome >= 108",
  "chromeAndroid >= 108",
  "edge >= 108",
  "firefox >= 115",
  "safari >= 16.4",
  "ios_saf >= 16.4",
];

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
