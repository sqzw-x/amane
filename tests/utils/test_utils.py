import pytest

from amane.enums import Language
from amane.utils.language import has_han, has_kana, has_letter, needs_llm_translation


@pytest.mark.parametrize(
    "s,expected",
    [
        ("", False),
        ("こんにちは", True),
        ("カタカナ", True),
        ("abc123", False),
        ("テスト123", True),
        ("Hello世界", False),
        ("ﾃｽﾄ", True),  # 半角片假名
        ("中文標題", False),  # 纯汉字: 句子级下不视为日文
        ("Rei Lakeside Lovers・神木麗", False),  # 中点 U+30FB 不在假名区间
    ],
)
def test_has_kana(s, expected):
    assert has_kana(s) == expected


@pytest.mark.parametrize(
    "s,expected",
    [
        ("", False),
        ("中文標題", True),
        ("後愛上你", True),
        ("吉高寧々", True),
        ("Hello世界", True),
        ("こんにちは", False),
        ("Hello world", False),
        ("ABC123", False),
    ],
)
def test_has_han(s, expected):
    assert has_han(s) == expected


@pytest.mark.parametrize(
    "s,expected",
    [
        ("", False),
        ("abc", True),
        ("ABC123", True),
        ("Café Müller", True),
        ("Привет", True),
        ("안녕하세요", True),
        ("中文", True),
        ("こんにちは", True),
        ("123", False),
        ("★★★", False),
        ("-_", False),
    ],
)
def test_has_letter(s, expected):
    assert has_letter(s) == expected


@pytest.mark.parametrize(
    "s,target,expected",
    [
        # 日文 → 各目标
        ("こんにちは世界", Language.ZH_CN, True),
        ("こんにちは世界", Language.EN, True),
        ("こんにちは世界", Language.JP, False),  # 已是日文
        # 英文 → 各目标
        ("Hello world", Language.ZH_CN, True),
        ("Hello world", Language.JP, True),
        ("Hello world", Language.EN, False),  # 已是英文
        # 含排版字符或变音字母的英文: 不得落入中文分支
        ("It won’t stop", Language.ZH_CN, True),  # U+2019
        ("Rain — and more…", Language.ZH_CN, True),  # U+2014 / U+2026
        ("Café Müller", Language.ZH_CN, True),
        ("Ready 😈", Language.ZH_CN, True),
        # 中文 (含简繁) → 一律不走 LLM
        ("你好世界", Language.ZH_CN, False),
        ("後愛上你", Language.ZH_CN, False),  # 繁→简由 zhconv 处理
        ("后爱上你", Language.ZH_TW, False),
        ("Rei Lakeside Lovers・神木麗", Language.ZH_CN, False),  # 中点不触发日文判定
        # 其他文字系统: 目标非英文时需要翻译
        ("Привет мир", Language.ZH_CN, True),
        ("안녕하세요", Language.ZH_CN, True),
        # 无字母: 数字与符号无需翻译
        ("", Language.ZH_CN, False),
        ("123-456", Language.ZH_CN, False),
        ("★★★★★", Language.ZH_CN, False),
        # 番号标题
        ("[HD] FC2-PPV-123456 {4K}", Language.ZH_CN, True),
        ("[HD] FC2-PPV-123456 {4K}", Language.EN, False),
    ],
)
def test_needs_llm_translation(s, target, expected):
    assert needs_llm_translation(s, target) == expected
