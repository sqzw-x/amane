---
name: amane-screenshot
description: >-
  用 scripts/docs_screenshots 生成或更新用户文档与 README 中的界面截图.
  Use when 新增、更新或重拍截图, 文档里需要配图, 界面改动后截图过期, or when asked to 截图 / screenshot.
---

# 文档截图

`scripts/docs_screenshots/` 先用合成数据集启动一个临时后端, 再用 Playwright 截图. 输出到 `docs/assets/screenshots/`: 每张截图都有浅色与深色两份, 深色文件名带 `-dark` 后缀. 运行方式见 `capture.py` 的模块 docstring.

- `seed.py`: 生成合成数据, 写入临时数据库与资源目录.
- `capture.py::build_shots`: 截图清单. 每项指定页面路由、截图前的交互、视口和裁切.

## 新增或更新截图

1. 截图需要的数据加到 `seed.py`. 只用占位内容: 番号 `ABC-001` 式, 标题 `标题 N`, 演员 `演员 X`, 图片为渐变底加文字. 不联网, 不调用 LLM.
2. 在 `build_shots` 加一项 `Shot`. 交互写成 `Page` 上的 async 函数, 按无障碍角色和界面文案定位元素.
3. 先构建前端 (后端直接提供 `web/dist`, 页面改动不构建就截不到), 再用 `--only <name>` 只截新增项. 排查时加 `--keep` 保留临时目录.
4. 浅色、深色两份都要逐张看过, 再嵌入文档.

## 入镜约束

- 不得出现内置刮削站点名. 必要时用 `Shot.clip_before` 截到相关区块上方为止.
- 不得出现本机临时路径 (`/var/folders`、`/private/tmp`). 种子数据中的库路径与文件路径写成 `/media/...`.
- 只用合成数据, 不读写 `./data`, 不占用 8000 端口.

## 嵌入

- `docs/` 下每张图写成一对, 由文档站按当前主题切换:
  `![说明](../assets/screenshots/x.webp#only-light){ loading=lazy }`
  `![说明](../assets/screenshots/x-dark.webp#only-dark){ loading=lazy }`
- README 用 `<picture>` 加 `prefers-color-scheme: dark` 的 `<source>`.
- 嵌入后运行 `uv run --group docs mkdocs build --strict`.
