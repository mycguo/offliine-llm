"""Cutting text into the passages the library indexes (shared by build_library.py and myfiles.py)."""
import re


def split_passages(text, target=1500, hard=3000):
    """Cut an article into ~1,500-character passages on paragraph boundaries; returns (heading, passage)."""
    out, buf, size, heading, buf_heading = [], [], 0, "", ""
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue
        if para.startswith("#"):
            if buf and size > 200:
                out.append((buf_heading, "\n\n".join(buf)))
                buf, size = [], 0
            heading = para.lstrip("#").strip()
            if not buf:
                buf_heading = heading
            buf.append(para)
            size += len(para) + 2
            continue
        pieces = []
        while len(para) > hard:
            cut = para.rfind(". ", target // 2, hard)
            cut = cut + 1 if cut > 0 else hard
            pieces.append(para[:cut].strip())
            para = para[cut:].strip()
        pieces.append(para)
        for piece in pieces:
            if buf and size + len(piece) > target:
                out.append((buf_heading, "\n\n".join(buf)))
                buf, size, buf_heading = [], 0, heading
            buf.append(piece)
            size += len(piece) + 2
    if buf:
        out.append((buf_heading, "\n\n".join(buf)))
    return out
