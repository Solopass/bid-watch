"""Pull visible strings out of a text-based PDF.

The MSBA estimated-bid schedule is a normal text PDF. This reads the
content streams with the standard library (zlib) and turns glyph runs into
lines. It does not render pages and it does not handle scanned images.
"""

from __future__ import annotations

import re
import zlib


def pdf_to_text(data: bytes) -> str:
    chunks = []
    for stream in _streams(data):
        text = text_from_content(stream)
        if text.strip():
            chunks.append(text)
    return "\n".join(chunks)


def text_from_content(content: bytes) -> str:
    """Join Tj/TJ strings. A large negative kern inserts a line break."""
    pieces: list[str] = []
    for kind, value in _tokens(content):
        if kind == "s":
            pieces.append(value)
        elif value < -400:
            pieces.append("\n")
        elif value < -40:
            pieces.append(" ")
    text = "".join(pieces)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text


def _streams(data: bytes):
    for match in re.finditer(rb"stream\r?\n", data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        raw = data[start:end].rstrip(b"\r\n")
        try:
            yield zlib.decompress(raw)
            continue
        except zlib.error:
            pass
        if b"Tj" in raw or b"TJ" in raw:
            yield raw


def _tokens(content: bytes):
    index = 0
    length = len(content)
    while index < length:
        if content[index] == 0x28:
            text, index = _string(content, index)
            yield "s", text
            continue
        number = re.match(rb"-?\d+(?:\.\d+)?", content[index:])
        if number and (index == 0 or content[index - 1] in b" []\n\r\t"):
            yield "n", float(number.group(0))
            index += len(number.group(0))
            continue
        index += 1


def _string(content: bytes, index: int) -> tuple[str, int]:
    index += 1
    chars: list[str] = []
    while index < len(content):
        byte = content[index]
        if byte == 0x5C and index + 1 < len(content):
            nxt = content[index + 1]
            named = {ord("n"): "\n", ord("r"): "\r", ord("t"): "\t", ord("("): "(", ord(")"): ")", ord("\\"): "\\"}
            if nxt in named:
                chars.append(named[nxt])
                index += 2
                continue
            if nxt in b"01234567":
                digits = bytearray()
                while index + 1 < len(content) and len(digits) < 3 and content[index + 1] in b"01234567":
                    index += 1
                    digits.append(content[index])
                chars.append(chr(int(digits.decode(), 8)))
                index += 1
                continue
        if byte == 0x29:
            return "".join(chars), index + 1
        chars.append(chr(byte))
        index += 1
    return "".join(chars), index
