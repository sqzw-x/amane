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

你在帮用户通过命令行管理一个 Amane 实例 (媒体元数据管理服务)
实例地址 = 本文档 URL 的 origin, 例如 http://localhost:8000

步骤如下, 每个阶段动手前先向用户说明, 必要时征求同意.

## 阶段一: 安装 CLI

Amane 通过 CLI 工具提供外部控制接口, 需单独在本机安装

1. 检查本机是否已安装 CLI 工具: 运行 `amane --version`. 若已安装跳过此步骤

2. 判断本机系统及架构 (darwin / linux / windows, amd64 / arm64),
   从 GitHub Releases 下载对应产物并校验 SHA256SUMS:
   https://github.com/sqzw-x/amane/releases/latest/download/amane-{os}-{arch}
   用 curl/wget 等下载, 不要用浏览器 — macOS 下浏览器给文件加隔离属性, 导致无法运行.

3. 就安装位置提供建议并征求用户同意, 例如 ~/.local/bin/amane
   (Windows 的 %LOCALAPPDATA%\\Amane\\bin\\amane.exe) 等
   装到 PATH 中的路径下以方便调用并避免重复安装
   需修改 PATH 时, 给出预览经用户同意后执行, 禁止擅自改系统配置.

4. 鉴权. 分情况:
   - 本机 localhost 上运行的桌面版: CLI 会自动读取实例数据目录里的 token, 用户无需操作.
   - 否则需要用户手动登录一次: 运行 `amane login --server <实例地址>`,
     CLI 会提示用户粘贴 token. token 的来源: 桌面应用 菜单 - 复制 API Token,
     或服务端启动日志, 或数据目录下的 token 文件.
     让用户自己粘贴. 禁止向用户索要 token 后代为输入, 禁止读取或回显它.

## 阶段二: 安装 Skill

1. 就 skill 安装位置提供建议并征求用户同意, 这可能和用户安装的 agent 工具相关, 例如 ~/.agents/skills 等
   然后运行 `amane skill install --dir <path>`, 注意 CLI 会自动创建 <path>/amane 子目录并放置 skill 文件, 无需手动创建

2. 运行 `amane doctor` 应该通过

3. 取决于用户使用的 Agent 工具, skill 可能立刻可见或需要重启. 加载 skill 并按指引操作.
"""


@router.get("/agent-guide", response_class=PlainTextResponse)
async def get_agent_guide() -> PlainTextResponse:
    return PlainTextResponse(AGENT_GUIDE, headers={"Cache-Control": "public, no-cache"})
