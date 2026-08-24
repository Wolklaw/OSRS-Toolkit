"""A raw newline should never sit inside a quoted JS string literal in a template.

The bug this guards: a template-editing script built a JS string using Python's own escape
for a newline inside a non-raw string, so Python turned it into an actual newline byte before
the file was ever written. The template looked fine to read -- the string just wrapped across
two source lines -- but a literal newline inside a single- or double-quoted JS string is a
syntax error, and it broke the entire inline <script> block on that page. Every onclick
handler on it silently stopped existing: clicking any button did nothing, with no error
visible anywhere but the browser console, and nothing reaching the server to show up in its
logs either.

This is a static check over the template source, not a real JS parser -- it only needs to
catch "a quoted string literal spans more than one line," which is enough for the failure
mode above without taking on a JS toolchain for one guard. Comments and backtick template
literals are skipped first and deliberately: an apostrophe inside a comment's prose, or a
template literal that legitimately spans several lines, would otherwise read as exactly the
same shape as the real bug and drown it in false positives.

Every backslash used to build the patterns below comes from chr(92) rather than a literal
backslash typed in this file's own source. Reason: this test exists because one literal
backslash sequence was silently rewritten already, by Python's own string escaping. A second
one -- the word-boundary escape in an earlier draft of this exact file -- was then rewritten
again, this time into an actual backspace byte, by whatever layer decodes this tool's own
file-writing calls. Assembling backslashes arithmetically sidesteps both failure modes at
once, for the one file where getting this wrong would be a little too on the nose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "src" / "toolkit_web" / "templates"

NEWLINE = chr(10)
BACKSLASH = chr(92)
# What the regex engine reads as one literal backslash: it is its own escape character, so
# matching one occurrence of it -- inside a character class or as "any escaped char" -- takes
# two backslash bytes in the pattern text, not one. Getting this count right by eye is exactly
# what this file exists to stop relying on, hence the name rather than a bare BACKSLASH*2.
ESCAPED_BACKSLASH = BACKSLASH + BACKSLASH

SCRIPT_BLOCK = re.compile("<script[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)

BLOCK_COMMENT = "/" + BACKSLASH + "*.*?" + BACKSLASH + "*/"
LINE_COMMENT = "//[^" + NEWLINE + "]*"
TEMPLATE_LITERAL = "`(?:[^`" + ESCAPED_BACKSLASH + "]|" + ESCAPED_BACKSLASH + ".)*`"
SINGLE_QUOTED = "'(?:[^'" + ESCAPED_BACKSLASH + "]|" + ESCAPED_BACKSLASH + ".)*'"
DOUBLE_QUOTED = '"(?:[^"' + ESCAPED_BACKSLASH + "]|" + ESCAPED_BACKSLASH + '.)*"'

# Named so the loop below can tell a real string literal from a comment or a template literal
# it walked past on the way -- both of the latter are allowed to span lines, so only the
# groups named "single" and "double" are ever checked for an embedded newline.
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
