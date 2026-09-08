# 文件发现

> 入口: `src/amane/scheduler/`. 监视器线程与防抖见源码; 本文只写通道分流与 CloudDrive webhook 契约.
> 入库归属见 [data-model.md](data-model.md), 任务入队见 [task-system.md](task-system.md).

## 两条发现通道

`Library.ingest` 选择该库如何发现新文件. `automation=none` 时两条都不收事件.

| ingest | 事件源 | Observer |
|--------|--------|----------|
| `native` (默认) | watchdog (`Observer` 或 `PollingObserver`) | 该库 `schedule` 到进程内**唯一** FileWatcher |
| `clouddrive` | `POST /api/webhooks/clouddrive` | **不** schedule. 避免 FUSE 挂载上没有 `FileCreated` 的 115 侧落盘被漏掉, 也避免与 webhook 双通道重复入库 |

`use_polling` 仍是进程级、仅作用于 native 库, 不能按库混用 FSEvents 与轮询.

## CloudDrive 路径

Webhook 的 `source_file` / `destination_file` 是 CloudDrive **虚拟路径** (POSIX, 以 `/` 分段, 如 `/115open/云下载/a.mp4`), 不是宿主挂载路径 (`/Volumes/115/...` 或 `X:\...`). Windows 上同样是这套 VFS 字符串; 不允许用本机 `pathlib.Path` 去解析 `/115open/...`.

`Library.cloud_path` 是该库根在 VFS 里的对应前缀, `ingest=clouddrive` 时必填. 本地 `Library.path` 仍是可扫描的挂载目录. 分流: 规范化后对所有 clouddrive 且 `automation≠none` 的库做最长 `cloud_path` 前缀匹配, 再按 posix 段拼到 `Library.path`. 创建 / 更新时拒绝与其它 clouddrive 库相同或互为前缀的 `cloud_path` (等长重复与嵌套均冲突; `/115open/a` 与 `/115open/b` 允许).

`mount_point_watcher` 的 `{mount_point}` 是宿主挂载点, 本通道不读.

native Observer 启动失败 (如 inotify 配额) 只停掉 FileWatcher, 已登记的 cloud 路由与 webhook 接收仍保留. `add_library` 热启动失败同样清掉未启动的实例, 不把 `_watcher` 留成半开状态.

## Webhook 载荷

端点在 `/api` 下, 鉴权与其它接口相同 (`Bearer` / cookie). CloudDrive 模板把 `Authorization` 设为 `Bearer <AMANE_TOKEN>`.

`data[]` 可批量. `action` 为 `create` / `delete` / `rename` (大小写不敏感). `is_dir` 可为 JSON 布尔或字符串 `"true"` / `"false"`. 未知顶层字段忽略.

处理在后台任务中执行, HTTP 立即 204, 避免 FUSE 子树扫描堵住 CloudDrive.

| 事件 | 行为 |
|------|------|
| 文件 create | `LibraryScan.classify` 为媒体则 `register_media_file` (已存在则跳过) |
| 目录 create | 防抖后对该子树 `scan_library` (与 watchdog 在目录移入时合成子文件同一范围: 只扫事件路径, 不是整库 REFRESH). `recursive=false` 的库只接受库根上的目录事件 |
| 文件 delete | 按本地路径删索引 |
| 目录 delete | 按路径前缀删该库索引, 不遍历磁盘 |
| 文件 rename | 更新路径; 源不在索引则按目标登记; 跨库则删源索引并在目标库登记 |
| 目录 rename | 前缀改写索引路径; 移出库当删除, 移入库与目录 create 同一批防抖后再扫; 跨库按目标库规则重新分类 |

目录事件可能早于目录缓存列出文件, 因此扫描前等待 `watcher.debounce_seconds`. 防抖结束后按**当前** `_cloud_routes` 重算本地路径: 库已删除、或 `cloud_path` / recursive 已不再覆盖该目录则跳过. 文件级事件与目录展开重叠时靠路径 UNIQUE 与「已登记则跳过」幂等.

目录跨库 rename 按目标库的 recursive / glob / 黑名单 / 预告片 / 体积规则重新判定; 不接受的文件只删源索引、不写入目标库.

本机经 FUSE 写入仍会推文件级 webhook; 该库不挂 Observer, 只走这一条.

CloudDrive 完全不推送的变更 (若发生) 此通道看不见, 仍可用 REFRESH.
