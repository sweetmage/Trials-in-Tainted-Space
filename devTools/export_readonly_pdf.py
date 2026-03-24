#!/usr/bin/env python3
"""Export a read-only TiTS script digest as a PDF.

The PDF contains:
1) All static dialogue fragments found in output(...) calls.
2) All static image identifiers found in showImage(...) calls.

This intentionally removes interactive game elements and produces a
linear, read-only artifact.
"""

from __future__ import annotations

import argparse
import ast
import html
import re
from pathlib import Path
from typing import Iterable, List, Sequence

OUTPUT_CALL_RE = re.compile(r"\boutput\s*\(")
SHOW_IMAGE_RE = re.compile(r"\bshowImage\s*\(")
STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')


class PdfWriter:
    """A tiny dependency-free PDF writer for text pages."""

    def __init__(self, page_width: int = 612, page_height: int = 792, margin: int = 54):
        self.page_width = page_width
        self.page_height = page_height
        self.margin = margin
        self.font_size = 10
        self.leading = 14

    @staticmethod
    def _escape_pdf_text(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    def _wrap(self, text: str, max_chars: int) -> List[str]:
        words = text.split()
        if not words:
            return [""]

        lines: List[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if len(candidate) <= max_chars:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def write(self, path: Path, lines: Sequence[str]) -> None:
        usable_width = self.page_width - (self.margin * 2)
        approx_char_width = self.font_size * 0.52
        max_chars = max(20, int(usable_width / approx_char_width))

        wrapped: List[str] = []
        for line in lines:
            clean = line.rstrip()
            wrapped.extend(self._wrap(clean, max_chars))

        lines_per_page = max(20, int((self.page_height - (self.margin * 2)) / self.leading))

        pages: List[List[str]] = []
        for i in range(0, len(wrapped), lines_per_page):
            pages.append(wrapped[i : i + lines_per_page])

        objects: List[bytes] = []

        def add_object(data: bytes) -> int:
            objects.append(data)
            return len(objects)

        font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

        content_ids: List[int] = []
        page_ids: List[int] = []

        for page_lines in pages:
            y_start = self.page_height - self.margin
            text_parts = [b"BT", f"/F1 {self.font_size} Tf".encode(), f"1 0 0 1 {self.margin} {y_start} Tm".encode()]

            for idx, line in enumerate(page_lines):
                escaped = self._escape_pdf_text(line)
                if idx == 0:
                    text_parts.append(f"({escaped}) Tj".encode())
                else:
                    text_parts.append(f"0 -{self.leading} Td ({escaped}) Tj".encode())

            text_parts.append(b"ET")
            stream_data = b"\n".join(text_parts)
            content_id = add_object(
                b"<< /Length " + str(len(stream_data)).encode() + b" >>\nstream\n" + stream_data + b"\nendstream"
            )
            content_ids.append(content_id)

            page_id = add_object(
                (
                    "<< /Type /Page /Parent {PAGES} 0 R /MediaBox [0 0 "
                    f"{self.page_width} {self.page_height}] /Resources << /Font << /F1 {font_id} 0 R >> >> "
                    f"/Contents {content_id} 0 R >>"
                ).encode()
            )
            page_ids.append(page_id)

        kids_refs = " ".join(f"{pid} 0 R" for pid in page_ids)
        pages_id = add_object(f"<< /Type /Pages /Kids [ {kids_refs} ] /Count {len(page_ids)} >>".encode())

        for idx, page_id in enumerate(page_ids):
            objects[page_id - 1] = objects[page_id - 1].replace(b"{PAGES}", str(pages_id).encode())

        catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

        chunks: List[bytes] = [b"%PDF-1.4\n"]
        offsets: List[int] = [0]
        current_offset = len(chunks[0])

        for i, obj in enumerate(objects, start=1):
            offsets.append(current_offset)
            body = f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
            chunks.append(body)
            current_offset += len(body)

        xref_offset = current_offset
        xref_lines = [f"0 {len(objects) + 1}\n", "0000000000 65535 f \n"]
        for off in offsets[1:]:
            xref_lines.append(f"{off:010d} 00000 n \n")

        trailer = (
            b"xref\n"
            + "".join(xref_lines).encode()
            + b"trailer\n"
            + f"<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n".encode()
            + b"startxref\n"
            + str(xref_offset).encode()
            + b"\n%%EOF\n"
        )

        chunks.append(trailer)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(chunks))


def _decode_as3_string(token: str) -> str:
    try:
        raw = ast.literal_eval(token)
    except (SyntaxError, ValueError):
        raw = token[1:-1]
    return html.unescape(raw)


def _extract_call_blob(source: str, start_index: int) -> str:
    open_paren = source.find("(", start_index)
    if open_paren == -1:
        return ""

    depth = 0
    in_string = False
    escaped = False
    for i in range(open_paren, len(source)):
        ch = source[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return source[open_paren + 1 : i]

    return ""


def _extract_dialogue_from_file(path: Path) -> List[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    results: List[str] = []

    for match in OUTPUT_CALL_RE.finditer(text):
        blob = _extract_call_blob(text, match.start())
        if not blob:
            continue

        fragments = [_decode_as3_string(m.group(0)) for m in STRING_RE.finditer(blob)]
        if not fragments:
            continue

        joined = "".join(fragments)
        joined = re.sub(r"\[[^\]]+\]", "", joined)
        joined = re.sub(r"<[^>]+>", "", joined)
        joined = joined.replace("\\n", "\n")
        joined = re.sub(r"\s+", " ", joined).strip()

        if joined:
            results.append(joined)

    return results


def _extract_images_from_file(path: Path) -> List[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    ids: List[str] = []

    for match in SHOW_IMAGE_RE.finditer(text):
        blob = _extract_call_blob(text, match.start())
        if not blob:
            continue

        for token in STRING_RE.findall(blob):
            img_id = _decode_as3_string(token).strip()
            if img_id:
                ids.append(img_id)

    return ids


def _iter_source_files(root: Path) -> Iterable[Path]:
    for folder in ("classes", "includes"):
        base = root / folder
        if not base.exists():
            continue
        yield from base.rglob("*.as")


def build_lines(root: Path) -> List[str]:
    dialogue: List[str] = []
    images: List[str] = []

    for file_path in _iter_source_files(root):
        dialogue.extend(_extract_dialogue_from_file(file_path))
        images.extend(_extract_images_from_file(file_path))

    unique_images = sorted(set(images))

    lines: List[str] = [
        "Trials in Tainted Space: Read-Only Narrative Export",
        "",
        "This document removes interactive gameplay elements and provides a linear reading export.",
        "Dialogue has been extracted from static output(...) strings in ActionScript source.",
        "",
        f"Dialogue fragments extracted: {len(dialogue)}",
        f"Unique image IDs referenced: {len(unique_images)}",
        "",
        "=== IMAGE REFERENCES ===",
    ]
    for img in unique_images:
        lines.append(f"- {img}")

    lines.extend(["", "=== DIALOGUE EXCERPTS ==="])
    for idx, block in enumerate(dialogue, start=1):
        lines.append(f"{idx:05d}. {block}")

    return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a read-only TiTS PDF with dialogue and image references.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Repository root")
    parser.add_argument("--output", type=Path, default=Path("exports/tits_read_only_dialogue.pdf"), help="Output PDF path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    lines = build_lines(args.root)

    writer = PdfWriter()
    writer.write(args.output, lines)

    print(f"Wrote {args.output} with {len(lines)} lines.")


if __name__ == "__main__":
    main()
