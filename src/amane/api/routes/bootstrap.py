"""接入引导: 免鉴权的固定文本.

挂在 ``/api`` 下并由 ``middleware.py::_TOKEN_EXEMPT`` 豁免, 与 ``/api/health`` 同一套做法:
免鉴权是显式登记的一条, 不靠路径前缀区分.

只回常量 —— 不拼接实例地址、版本、路径或 token. 实例地址由调用方从请求 URL 推得, 见
``AGENT_GUIDE`` 首段. 正文随发布更新, 不含任何会随实例变化的内容.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["agent-guide"])

AGENT_GUIDE = """# 接入 Amane

你在帮用户用命令行管理一个 Amane 实例 (媒体元数据管理服务).
实例地址 = 本文来源 URL 的 origin; 拿不到来源 URL 时问用户.

动手前先向用户说明, 必要时征求同意.

## 阶段一: 安装 CLI

CLI 可管理多个实例, 每个实例是一份 profile, 存在 CLI 配置文件里.

1. 运行 `amane --version`, 已安装则跳过下一步.

2. 按本机系统与架构从 GitHub Releases 取 amane-{os}-{arch} 并校验 SHA256SUMS:
   https://github.com/sqzw-x/amane/releases/latest/download/amane-{os}-{arch}
   只有 linux/darwin 的 amd64 与 arm64, 以及 Windows amd64 (加 .exe), 其余组合没有产物.
   用 curl/wget 下载, 不要用浏览器: macOS 下浏览器会加隔离属性, 导致无法运行.
   chmod +x (Windows 无需) 后移到第 3 步选定的位置.

3. 就安装位置征求同意, 建议 PATH 内的路径, 例如 ~/.local/bin/amane
   (Windows 为 %LOCALAPPDATA%\\Programs\\amane\\amane.exe).
   要改 PATH 时先给预览并征得同意, 不要擅自改系统配置.

4. 运行 `amane config list`, 已有 server 等于实例地址的 profile 就记下名字,
   用 `amane doctor --profile <名字>` 验证; 通过则直接进入阶段二.

5. 登录. profile 名取实例地址的主机与端口, 例如 localhost-5173.
   - 本机 (127.0.0.1 / localhost / ::1): `amane login --server <实例地址> --profile <名字>`,
     CLI 会自己找桌面版留下的 token 并当场验证, 用户无需粘贴. 失败则按下面处理.
   - 其它情况: 不要代跑, 也不要向用户索要 token, 请用户在自己的终端执行同一条命令并粘贴
     (token 可在桌面应用菜单「复制 API Token」或启动日志里找到).
     不要从文件、日志、进程环境、`ps` 或源码里翻 token; 拿不到就停下来等用户.

## 阶段二: 安装 Skill

1. 可用 skill 里已有 `amane` 就直接加载, 本阶段结束.

2. 就安装位置征求同意 (常见 ~/.agents/skills), 运行 `amane skill install --dir <path>`.
   它只由 CLI 生成, 不连接实例也不用给 profile; 生成的命令用 <名字> 占位, 由你填上正在操作的 profile.

3. 运行 `amane doctor --profile <名字>` 应当通过.

4. 视 Agent 工具而定, skill 可能需要重启才可见; 加载后按它的指引操作.

本文档到此结束.
"""


@router.get("/agent-guide", response_class=PlainTextResponse)
async def get_agent_guide() -> PlainTextResponse:
    return PlainTextResponse(AGENT_GUIDE, headers={"Cache-Control": "public, no-cache"})
