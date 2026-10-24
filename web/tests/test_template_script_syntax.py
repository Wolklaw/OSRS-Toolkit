from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "src" / "toolkit_web" / "templates"

NEWLINE = chr(10)
BACKSLASH = chr(92)
ESCAPED_BACKSLASH = BACKSLASH + BACKSLASH

SCRIPT_BLOCK = re.compile("<script[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)

BLOCK_COMMENT = "/" + BACKSLASH + "*.*?" + BACKSLASH + "*/"
LINE_COMMENT = "//[^" + NEWLINE + "]*"
TEMPLATE_LITERAL = "`(?:[^`" + ESCAPED_BACKSLASH + "]|" + ESCAPED_BACKSLASH + ".)*`"
SINGLE_QUOTED = "'(?:[^'" + ESCAPED_BACKSLASH + "]|" + ESCAPED_BACKSLASH + ".)*'"
DOUBLE_QUOTED = '"(?:[^"' + ESCAPED_BACKSLASH + "]|" + ESCAPED_BACKSLASH + '.)*"'

TOKEN = re.compile(
    "(?P<comment>" + BLOCK_COMMENT + "|" + LINE_COMMENT + ")"
    "|(?P<template>" + TEMPLATE_LITERAL + ")"
    "|(?P<single>" + SINGLE_QUOTED + ")"
    "|(?P<double>" + DOUBLE_QUOTED + ")",
    re.DOTALL,
)

TEMPLATE_FILES = sorted(TEMPLATES_DIR.glob("*.html"))


@pytest.mark.parametrize("path", TEMPLATE_FILES, ids=lambda p: p.name)
def test_no_js_string_literal_spans_a_raw_newline(path: Path):
    source = path.read_text(encoding="utf-8")
    for script_match in SCRIPT_BLOCK.finditer(source):
        script = script_match.group(1)
        for token_match in TOKEN.finditer(script):
            if token_match.lastgroup not in ("single", "double"):
                continue
            literal = token_match.group(0)
            assert NEWLINE not in literal, (
                f"{path.name}: a JS string literal contains a raw newline instead of an "
                f"escaped backslash-n, which is a syntax error that breaks every handler "
                f"in the block: {literal[:80]!r}..."
            )
