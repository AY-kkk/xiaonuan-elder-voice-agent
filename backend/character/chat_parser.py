"""聊天记录上传解析。

解析结果只用于当次人格蒸馏，不落库。支持常见导出格式：
txt/md/json/csv/html/docx/xlsx，以及安装 pypdf 后的 pdf。
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from html.parser import HTMLParser
from typing import Any
from xml.etree import ElementTree

SUPPORTED_CHAT_EXTENSIONS = {
    "txt",
    "md",
    "markdown",
    "json",
    "csv",
    "html",
    "htm",
    "docx",
    "xlsx",
    "pdf",
}


class ChatParseError(ValueError):
    pass


def parse_chat_record(filename: str, data: bytes, *, max_chars: int = 12000) -> str:
    ext = _extension(filename)
    if ext not in SUPPORTED_CHAT_EXTENSIONS:
        raise ChatParseError(f"暂不支持该聊天记录格式：{ext or '未知'}")
    if not data:
        raise ChatParseError("聊天记录文件为空")

    try:
        if ext in {"txt", "md", "markdown"}:
            text = _decode_text(data)
        elif ext == "json":
            text = _parse_json(data)
        elif ext == "csv":
            text = _parse_csv(data)
        elif ext in {"html", "htm"}:
            text = _parse_html(data)
        elif ext == "docx":
            text = _parse_docx(data)
        elif ext == "xlsx":
            text = _parse_xlsx(data)
        elif ext == "pdf":
            text = _parse_pdf(data)
        else:
            text = ""
    except ChatParseError:
        raise
    except Exception as exc:
        raise ChatParseError("聊天记录解析失败，可尝试导出为 txt、csv 或 html 后重试") from exc

    text = _normalize(text)
    if not text:
        raise ChatParseError("没有从聊天记录中解析出可用文本")
    return text[:max_chars]


def _extension(filename: str) -> str:
    name = (filename or "").lower().strip()
    return name.rsplit(".", 1)[1] if "." in name else ""


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _parse_json(data: bytes) -> str:
    parsed = json.loads(_decode_text(data))
    lines: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            speaker = (
                value.get("speaker")
                or value.get("sender")
                or value.get("from")
                or value.get("name")
                or ""
            )
            content = (
                value.get("content")
                or value.get("text")
                or value.get("message")
                or value.get("msg")
                or ""
            )
            if content:
                lines.append(f"{speaker}: {content}" if speaker else str(content))
            else:
                for item in value.values():
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            lines.append(value)

    walk(parsed)
    return "\n".join(lines)


def _parse_csv(data: bytes) -> str:
    text = _decode_text(data)
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    rows = csv.DictReader(io.StringIO(text), dialect=dialect)
    lines: list[str] = []
    if rows.fieldnames:
        for row in rows:
            speaker = _pick(row, ("speaker", "sender", "from", "name", "昵称", "发送者"))
            content = _pick(row, ("content", "text", "message", "msg", "消息", "内容"))
            if content:
                lines.append(f"{speaker}: {content}" if speaker else content)
    if lines:
        return "\n".join(lines)
    raw_rows = csv.reader(io.StringIO(text), dialect=dialect)
    return "\n".join(" ".join(cell.strip() for cell in row if cell.strip()) for row in raw_rows)


def _parse_html(data: bytes) -> str:
    parser = _TextHTMLParser()
    parser.feed(_decode_text(data))
    return "\n".join(parser.parts)


def _parse_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for para in root.findall(".//w:p", ns):
        texts = [node.text or "" for node in para.findall(".//w:t", ns)]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _parse_xlsx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared = _xlsx_shared_strings(zf)
        names = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
        lines: list[str] = []
        for name in names[:5]:
            root = ElementTree.fromstring(zf.read(name))
            for row in root.findall(".//{*}row"):
                cells = []
                for cell in row.findall("{*}c"):
                    val = _xlsx_cell_text(cell, shared)
                    if val:
                        cells.append(val)
                if cells:
                    lines.append(" ".join(cells))
        return "\n".join(lines)


def _parse_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:
        raise ChatParseError("PDF 解析需要安装 pypdf；可先导出为 txt/docx/html") from exc
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages[:20])


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
    values = []
    for item in root.findall(".//{*}si"):
        values.append("".join((node.text or "") for node in item.findall(".//{*}t")))
    return values


def _xlsx_cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    value = cell.find("{*}v")
    if value is None or value.text is None:
        inline = cell.find(".//{*}t")
        return (inline.text or "").strip() if inline is not None else ""
    raw = value.text.strip()
    if cell.attrib.get("t") == "s":
        try:
            return shared[int(raw)].strip()
        except (ValueError, IndexError):
            return ""
    return raw


def _pick(row: dict, keys: tuple[str, ...]) -> str:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        val = row.get(key) or lowered.get(key.lower())
        if val:
            return str(val).strip()
    return ""


def _normalize(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"p", "div", "li", "br", "tr"}:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "tr"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if cleaned:
            self._buf.append(cleaned)

    def _flush(self) -> None:
        if self._buf:
            self.parts.append(" ".join(self._buf))
            self._buf = []
