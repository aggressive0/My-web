"""
文档加载（Phase 6.2）。

本阶段只支持 txt / md / html —— 这是用户明确选择的范围，不是遗漏。
遇到 .pdf / .docx 时**抛出明确错误**，绝不静默跳过：

- 方案 §5.2 要求「解析失败必须返回错误信息，不允许静默跳过」；
- 扫描版 PDF 需要 OCR，普通文本提取拿不到字，假装解析成功会污染知识库。

编码探测按 utf-8-sig → utf-8 → gb18030 顺序尝试。gb18030 是 gbk 的超集，
排在最后，避免把 UTF-8 中文误判成别的编码。
"""

import os

MAX_BYTES = 20 * 1024 * 1024        # 20MB，超过直接拒绝（方案 §17.1）

SUPPORTED_EXTENSIONS = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
}

_OCR_NOTE = "扫描版 PDF 即使后续支持解析也仍需 OCR，不能假设文本提取能识别图片中的文字。"


class UnsupportedFormat(ValueError):
    """格式不受支持。继承 ValueError，路由层按参数错误处理（400）。"""


def _unsupported_message(ext):
    return ("暂不支持的格式 %s：本阶段只支持 txt / md / html。"
            "后续批次再接入 pdf / docx；%s" % (ext or "(无扩展名)", _OCR_NOTE))


def _decode(data):
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    # 三种都失败：用替换字符保留结构，但如实标出来，不假装解码成功
    return data.decode("utf-8", "replace"), "utf-8(replace)"


def load_from_bytes(filename, data):
    """返回 {file_name, extension, kind, text, encoding, size_bytes}。"""
    if data is None:
        raise ValueError("文件内容为空")
    if len(data) > MAX_BYTES:
        raise ValueError("文件过大（%d 字节），上限 %d 字节" % (len(data), MAX_BYTES))

    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormat(_unsupported_message(ext))

    text, encoding = _decode(data)
    if not text.strip():
        raise ValueError("文件解析后为空文本：%s" % (filename or "(未命名)"))

    return {
        "file_name": filename or "",
        "extension": ext,
        "kind": SUPPORTED_EXTENSIONS[ext],
        "text": text,
        "encoding": encoding,
        "size_bytes": len(data),
    }


def load_from_path(path):
    """从磁盘读取。路径由调用方控制（脚本 / CLI），不接受来自 HTTP 的任意路径。"""
    if not os.path.isfile(path):
        raise ValueError("文件不存在：%s" % path)
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        raise ValueError("文件过大（%d 字节），上限 %d 字节" % (size, MAX_BYTES))
    with open(path, "rb") as f:
        data = f.read()
    return load_from_bytes(os.path.basename(path), data)
