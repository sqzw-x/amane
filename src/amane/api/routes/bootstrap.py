"""接入引导: 免鉴权的固定文本.

它是唯一公开的端点, 因此只回常量 —— 不拼接实例地址、版本、路径或 token. 实例地址由调用方
从请求 URL 推得, 见 ``AGENT_GUIDE`` 首段. 正文随发布更新, 不含任何会随实例变化的内容.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

public_router = APIRouter(tags=["agent-guide"])

AGENT_GUIDE = """# 接入 Amane

你在帮用户通过命令行管理一个 Amane 实例 (媒体元数据管理服务).
实例地址 = 你获取本文档所用 URL 的 origin, 例如 http://127.0.0.1:8000.

按两个阶段进行. 每个阶段动手前先向用户说明, 并按下面要求征求同意.

## 阶段一: 安装 CLI

1. 判断本机平台 (darwin / linux / windows, amd64 / arm64),
   从 GitHub Releases 下载对应产物并校验 SHA256SUMS:
   https://github.com/sqzw-x/amane/releases/latest/download/amane-{os}-{arch}
   用 curl 下载, 不要用浏览器打开链接 — macOS 会给下载物加隔离属性, 导致无法运行.

2. 安装位置要问用户, 不要自己决定. 建议 ~/.local/bin/amane
   (Windows 为 %LOCALAPPDATA%\\Amane\\bin\\amane.exe), 并告知:
   装到已有 PATH 目录则装完可直接调用; 否则之后都用绝对路径调用.
   需要修改 PATH 时, 先给出要追加的那一行, 经用户同意后再写, 不要擅自改 shell 配置.

3. 鉴权. 先判断属于哪种情况, 再决定是否打断用户:
   - 本机就是运行实例的机器: CLI 会自动读取实例数据目录里的 token, 用户无需操作.
   - 否则需要用户手动登录一次: 运行 `amane login --server <实例地址>`,
     CLI 会提示用户粘贴 token (token 在实例 Web 界面的「连接智能体」页可复制).
     让用户自己粘贴. 不要向用户索要 token 后代为输入, 也不要读取或回显它.

4. 运行 `amane doctor`. 全部通过才算阶段一完成; 有失败项就把输出原样交给用户.

## 阶段二: 安装 Skill

5. 运行 `amane skill install`. 它会写入 Skill 目录; 先问用户装到哪个目录,
   不确定就停下来问, 不要猜, 也不要往系统目录写.

6. 安装完成后读一遍完整说明. 此后所有操作都通过 CLI 完成.

## 约束

- 不要自己拼 HTTP 请求调用接口, 一律经由 CLI.
- 不要在对话里回显 token, 也不要写入任何文件或命令历史.
- 删除、合并、批量修改这类操作, 执行前先向用户复述影响面并取得明确同意.
- 需要查日志、配置或数据库时, 按 Skill 里的路径直接读文件, 不要为此调用接口.
"""


@public_router.get("/public/agent", response_class=PlainTextResponse)
async def get_agent_guide() -> PlainTextResponse:
    return PlainTextResponse(AGENT_GUIDE, headers={"Cache-Control": "public, no-cache"})
