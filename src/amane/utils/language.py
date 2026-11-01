"""文本语言判定启发式.

仅服务于"目标语言是否已满足, 是否需要翻译"的决策, 不追求通用语言识别.
适用范围是句子级文本 (标题 / 简介), 判据取字符证据:
- 假名: 句子级日文必然出现, 纯汉字写法只出现在词级 (标签/人名), 不在适用范围内.
- 汉字: 与日文共用, 故须先排除假名. 中文简繁是字形差异, 由内置 zhconv 完成, 不经 LLM.
- 其余含字母: 其他语系, 按目标语言决定是否翻译. 无字母的数字与符号无需翻译.
"""

import re

from ..enums import Language

# 平假名 + 片假名 + 半角片假名 (U+FF66-FF9D). 区间不得覆盖中点 U+30FB: 中文标题常用它分隔, 覆盖会把中文判成日文.
KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30ff\uff66-\uff9d]")

# 汉字: 基本区 + 扩展 A + 兼容区. 日文汉字同样命中, 故只在无假名时作为"中文"的判据.
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# 任意文字系统的字母 (拉丁 / 西里尔 / 韩文等), 不含数字与标点.
LETTER = re.compile(r"[^\W\d_]")


def has_kana(s: str) -> bool:
    """文本是否含假名 (平/片/半角片假名). 句子级文本中等价于"是日文"."""
    return bool(KANA.search(s))


def has_han(s: str) -> bool:
    """文本是否含汉字. 日文汉字同样命中, 调用方须先排除假名."""
    return bool(HAN.search(s))


def has_letter(s: str) -> bool:
    """文本是否含任意字母. 纯数字与纯符号返回 ``False``."""
    return bool(LETTER.search(s))


def needs_llm_translation(s: str, target: Language) -> bool:
    """文本译至 ``target`` 是否需要 LLM (跨语系).

    假名判日文, 汉字判中文; 中文 (含简繁互转, 共用字) 一律返回 ``False``, 交由 zhconv 处理.
    其余含字母的文本按目标语言判定; 无字母 (数字 / 符号 / emoji) 无需翻译.
    """
    if not s:
        return False
    if has_kana(s):
        return target != Language.JP
    if has_han(s):
        return False
    if has_letter(s):
        return target != Language.EN
    return False
