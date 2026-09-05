---
name: amane-dev
description: >-
  Starts Amane local API + Vite via `just dev`. Use when the user asks
  to just dev, 启动开发服务器, 起前端, or start the dev servers.
---

# 开发服务器

`just dev` 先 `generate` 再并行起 API 与 Vite. 后台启动. 禁止等待 Vite 的 `Local:` / `ready in` — just 并行输出经常没有这两行.

就绪以 HTTP 为准. 探测与回显一律用 `localhost`, 禁止 `127.0.0.1` (进程不一定监听 IPv4).

| 进程 | 就绪 |
|------|------|
| API | `GET http://localhost:${AMANE_PORT:-8000}/api/health` 返回 200; 日志 `amane service ready` 亦可 |
| Web | `GET http://localhost:5173/` 返回 200 |

已在监听则禁止再启动一份. 就绪后回显:

```
API: http://localhost:8000
Web: http://localhost:5173
```

`AMANE_PORT` 或 Vite 占用顺延时, 按实际端口写.
