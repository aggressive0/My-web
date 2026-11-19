"""
文档清洗（Phase 6.2）。

原则：只做「确定、可解释」的清理——去控制字符、折叠空白、抽 HTML 正文。
不做摘要、不改写、不删「看起来无关」的段落。

理由很直接：清洗掉的内容无法追溯回原文，而本项目的验收标准之一是
每条检索证据都能对上真实文档（writing-block.md §7.2）。任何有损清洗
都会在证据链上留一个无法解释的缺口。
"""

import re
from html.parser import HTMLParser

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INLINE_SPACE = re.compile(r"[ \t\u00a0\u3000]+")
_MANY_BLANKS = re.compile(r"\n{3,}")
_TRAILING = re.compile(r"[ \t]+\n")

# Steam 公告用的是 BBCode，不是 HTML。这些标记会被 BGE-M3 当成正文，
# 白白占掉语义空间（`[p]`、`[/*]`、`{STEAM_CLAN_IMAGE}/...` 都是）。
# 只删**已知的**标签，不碰通用方括号：Markdown 的 [文本](链接) 必须留着。
_BBCODE_MEDIA = re.compile(r"\[(?:img|previewyoutube|video)[^\]]*\].*?\[/(?:img|previewyoutube|video)\]",
                           re.I | re.S)
_BBCODE_URL_NAMED = re.compile(r"\[url=[^\]]*\](.*?)\[/url\]", re.I | re.S)
_BBCODE_URL_BARE = re.compile(r"\[url\](.*?)\[/url\]", re.I | re.S)
_BBCODE_TAG = re.compile(
    r"\[/?(?:b|i|u|s|p|h[1-6]|list|\*|quote|spoiler|noparse|hr|table|tr|td|th|"
    r"code|center|left|right|color|size|font)(?:[^\]]*)\]",
    re.I)
_STEAM_CLAN_IMAGE = re.compile(r"\{STEAM_CLAN_IMAGE\}[^\s\]\"']*")

_BLOCK_TAGS = frozenset((
    "p", "div", "br", "li", "tr", "td", "th", "section", "article", "header",
    "footer", "blockquote", "pre", "table", "ul", "ol",
    "h1", "h2", "h3", "h4", "h5", "h6",
))
_SKIP_TAGS = frozenset(("script", "style", "noscript", "template"))


class _HTMLTextExtractor(HTMLParser):
    """只抽可见正文与 <title>。不解析 CSS、不执行脚本、不跟随链接。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title_parts = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip_depth:
            self.parts.append(data)


def html_to_text(html):
    """返回 (title, text)。畸形 HTML 交给 HTMLParser 自行容错。"""
    parser = _HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    title = clean_text("".join(parser.title_parts))
    text = clean_text("".join(parser.parts))
    return title, text


def strip_bbcode(text):
    """去掉 Steam 公告里的 BBCode 标记，保留可读正文与链接文字。"""
    if not text:
        return ""
    text = _BBCODE_MEDIA.sub(" ", text)
    text = _BBCODE_URL_NAMED.sub(r"\1", text)
    text = _BBCODE_URL_BARE.sub(r"\1", text)
    text = _BBCODE_TAG.sub(" ", text)
    text = _STEAM_CLAN_IMAGE.sub(" ", text)
    return text


def clean_text(text):
    """折叠空白、去控制字符、收敛空行、剥离 BBCode。空输入返回空串，不抛异常。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = strip_bbcode(text)
    lines = [_INLINE_SPACE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = _TRAILING.sub("\n", text)
    text = _MANY_BLANKS.sub("\n\n", text)
    return text.strip()
