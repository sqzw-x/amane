/** `AppShell.Header` 的高度, 传给 AppShell; 页面不得另行书写该数值. */
export const APP_SHELL_HEADER_HEIGHT = 60;

/**
 * AppShell.Main 内容区高度: 视口高度扣除 header 与上下 padding.
 * 两个扣除项均取自 AppShell 写入 `:root` 的变量: 该变量以 rem 计算, 浏览器默认字号改变时仍与实际 header 同高,
 * 手写像素值会让钉高页面超出 Main 内容区并被裁掉.
 */
export const APP_SHELL_MAIN_HEIGHT =
  "calc(100dvh - var(--app-shell-header-height) - 2 * var(--app-shell-padding))" as const;
