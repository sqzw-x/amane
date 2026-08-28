# 常见问题

## Docker 里超分失败 / 缺 libgomp

官方镜像编进 patched waifu2x (默认 `waifu-photo-2x`): 有 Vulkan ICD 走 GPU, 没有则 `-g -1` 用 ncnn 原生 CPU, 不依赖 Mesa 软件 Vulkan. 运行时需要 `libgomp1` (OpenMP), 镜像已带.

`realesr-photo-4x` 仍要真 GPU. 无独显/核显时改用 `waifu-photo-2x`.

Intel/AMD 核显可把 `/dev/dri` 挂进容器并提供 Mesa ICD, 同一份二进制会走 GPU, 比 CPU 快很多.
