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

2. 判断本机系统及架构, 从 GitHub Releases 下载对应产物并校验 SHA256SUMS:
   https://github.com/sqzw-x/amane/releases/latest/download/amane-{os}-{arch}
   产物名就是 amane-{os}-{arch}, Windows 另加 .exe. 覆盖 linux/darwin 的 amd64 与 arm64,
   Windows 只有 amd64 — 其余组合没有产物, 不要按公式硬拼.
   用 curl/wget 等下载, 不要用浏览器 — macOS 下浏览器给文件加隔离属性, 导致无法运行.
   下载后 chmod +x (Windows 无需), 再移动到第 3 步选定的位置.

3. 就安装位置提供建议并征求用户同意, 例如 ~/.local/bin/amane
   (Windows 的 %LOCALAPPDATA%\\Amane\\bin\\amane.exe) 等
   装到 PATH 中的路径下以方便调用并避免重复安装
   需修改 PATH 时, 给出预览经用户同意后执行, 禁止擅自改系统配置.

4. 登录. 先判断 origin 的主机: 127.0.0.1 / localhost / ::1 算本机.
   - 本机: 运行 `amane login --server <origin>`. CLI 会自己去找桌面版留在数据目录里的 token,
     并当场验证一次; 成功即可继续, 用户不需要粘贴任何东西.
     若 login 失败 (本机没有可用凭据、或读不到 token), 不要重试, 按下面「不在本机」处理.
   - 不在本机, 或本机没有可用凭据: 不要代为执行, 也不要向用户索要 token. 请用户在自己的
     终端运行 `amane login --server <origin>` 并自行粘贴; 告诉用户可以去桌面应用菜单
     「复制 API Token」, 或实例的启动日志里找.
     你自己不要去取: 不读 token 文件, 不读日志里的 token, 也不从进程环境、`ps`、源码或
     任何其它途径翻找. 拿不到就停下来等用户, 这是唯一正确的做法. 用户完成后你再继续.

## 阶段二: 安装 Skill

1. 就 skill 安装位置提供建议并征求用户同意, 这可能和用户安装的 agent 工具相关, 例如 ~/.agents/skills 等
   然后运行 `amane skill install --dir <path>`, 注意 CLI 会自动创建 <path>/amane 子目录并放置 skill 文件, 无需手动创建

2. 运行 `amane doctor` 应该通过

3. 取决于用户使用的 Agent 工具, skill 可能立刻可见或需要重启. 加载 skill 并按指引操作.
"""


@router.get("/agent-guide", response_class=PlainTextResponse)
async def get_agent_guide() -> PlainTextResponse:
    return PlainTextResponse(AGENT_GUIDE, headers={"Cache-Control": "public, no-cache"})
