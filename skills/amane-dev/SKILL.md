---
name: amane-dev
description: >-
  Starts Amane local API + Vite via `just dev`. Use when the user asks
  to just dev, 启动开发服务器, 起前端, or start the dev servers.
---

# 开发服务器

`just dev` 先 `generate` 再并行起 API 与 Vite. 后台启动. 禁止等待 Vite 的 `Local:` / `ready in` — just 并行输出经常没有这两行.

就绪以 HTTP 为准. 探测一律用 `localhost`, 禁止 `127.0.0.1` (进程不一定监听 IPv4).

| 进程 | 就绪 |
|------|------|
| API | `GET http://localhost:${AMANE_PORT:-8000}/api/health` 返回 200; 日志 `amane service ready` 亦可 |
| Web | `GET http://localhost:5173/` 返回 200 |

已在监听则禁止再启动一份. `AMANE_PORT` 或 Vite 占用顺延时, 按实际端口写.

## 地址的输出

**只输出 Web 地址**, 且**只以纯文本给出**, 不放进代码块 —— 代码块内的 URL 不会渲染成可点击链接, 纯文本才会.

就绪后输出一行 `Web: http://localhost:5173` (行内代码只是本文件的排版, 实际回复里是裸文本).

API 地址只在探测就绪时使用, 不写给用户: 用户经浏览器访问前端, 请求由 Vite 代理到 `/api`.

要用户打开某个页面时, 给出带路径的完整地址, 同样纯文本, 例如 `Web: http://localhost:5173/meta/1393`.

## 让用户验证时

凡请用户手测、复验、确认现象, 回复里必须再打印一次 Web 地址与要打开的具体页面, 不允许只写「刷新页面」「再试一次」. 是否已在运行都要打印.
