#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
课表图片 → COURSES 代码（实验性）

用法:
    python3 tools/识别课表.py 课表截图.png [--force]

做法:
    1. 整图（或表格带）放大后识别一次，找到「星期一…星期日」表头；
    2. 按表头把图切成若干列，逐列再识别（避免相邻列文字被粘成一行）；
    3. 每列内按纵向间距聚成「格子」，从格子里抠课程名 / 节次 / 周次 / 教室。

质量门槛（见 README「图片要多清晰」）:
    识别后如果字太小（典型是教务系统整屏截图缩过），会直接拒绝并提示重新截图；
    确实想硬跑可以加 --force。

依赖: macOS 自带 Vision（tools/ocr.swift）+ Pillow（只用于切图）。不联网。
"""
import json
import os
import re
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from 课表解析 import (DAY_NAMES, header_index, weeks_expr,   # noqa: E402
                      slot_of, room_of, looks_like_course)

OCR_SWIFT = os.path.join(HERE, "ocr.swift")
WORK = "/tmp/timetable_ocr"
SCALE = 3                # 放大倍数：小字必须放大才认得准
MIN_TEXT_HEIGHT = 0.010  # 归一化文字高度下限，低于此值判定为"分辨率不足"


def ocr(image_path):
    out = subprocess.run(["swift", OCR_SWIFT, image_path],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("识别失败：\n" + out.stderr[-500:])
    return json.loads(out.stdout)


def find_layout(img, Image):
    """挑一个能认出最多表头的放大方案"""

    def band(a, b):
        return img.crop((0, int(img.height * a), img.width, int(img.height * b)))

    attempts = [
        ("full", img, 3),
        ("band", band(0.10, 0.65), 3),
        ("band2", band(0.05, 0.80), 3),
        ("band3", band(0.15, 0.55), 4),
        ("full2", img, 2),
    ]
    best = (None, [], [], 3, 0)
    for name, cand, scale in attempts:
        big = cand.resize((cand.width * scale, cand.height * scale), Image.LANCZOS)
        p = os.path.join(WORK, "layout_%s.png" % name)
        big.save(p)
        got = ocr(p)
        hs = [e for e in got if header_index(e["text"]) is not None]
        print("  布局探测 %-6s → %d 个表头" % (name, len(hs)))
        if len(hs) > best[4]:
            best = (cand, hs, got, scale, len(hs))
        if best[4] >= 7:
            break
    return best


def median_text_height(items, heads):
    """表头以下文字块高度的中位数，用来判断字够不够大"""
    if not heads:
        return 0.0
    y_min = min(h["y"] for h in heads)
    hs = [e["h"] for e in items if e["y"] < y_min - 0.01 and e["h"] > 0.0005]
    return statistics.median(hs) if hs else 0.0


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    if not args:
        sys.exit("用法: python3 tools/识别课表.py <课表图片> [--force]")
    src = args[0]

    try:
        from PIL import Image
    except ImportError:
        sys.exit("需要 Pillow：pip3 install pillow")

    os.makedirs(WORK, exist_ok=True)
    img = Image.open(src).convert("RGB")

    base, heads, items, scale_used, _ = find_layout(img, Image)
    if not heads:
        print("❌ 没找到「星期一…星期日」表头。可能不是课表，或版式差异太大。")
        print("   识别到的前几段文字：")
        for e in items[:10]:
            print("     ", e["text"])
        return 1

    # 换成「相对原图」的高度：裁剪 + 放大都不该影响判定
    th = median_text_height(items, heads) * (base.height / img.height)
    print("\n  文字高度（归一化）: %.4f   门槛 %.4f" % (th, MIN_TEXT_HEIGHT))
    if th < MIN_TEXT_HEIGHT and not force:
        print("\n❌ 这张图分辨率不足，字太小，识别会大量出错，先不往下跑。")
        print("   建议二选一：")
        print("     1) 重新截图：先把课表区域放大，再截那一块（不要截整屏）")
        print("     2) 有学校导出的 PDF / Excel，改用 tools/读取Excel课表.py")
        print("   确实要硬跑：加 --force，结果务必逐条核对")
        return 2

    heads.sort(key=lambda e: e["x"])
    img = base
    W = img.width * scale_used

    bounds = []
    for i, h in enumerate(heads):
        left = h["x"] * W
        right = (h["x"] + h["w"]) * W
        if i > 0:
            left = ((heads[i - 1]["x"] + heads[i - 1]["w"]) * W + left) / 2
        if i < len(heads) - 1:
            right = (right + heads[i + 1]["x"] * W) / 2
        else:
            right = W
        if i == 0:
            left = max(0, h["x"] * W - 0.035 * W)
        bounds.append((DAY_NAMES[header_index(h["text"])], int(left), int(right)))

    # 左边节次列的行位置（格子里读不到「(1-2节)」时兜底）
    rows = []
    for e in items:
        t = e["text"].strip()
        if e["x"] < 0.16 and re.fullmatch(r"\d{1,2}(?:[-~－]\d{1,2})?", t):
            rows.append((t, e["y"] + e["h"] / 2))

    def slot_at(y):
        if not rows:
            return None
        text, ry = min(rows, key=lambda r: abs(r[1] - y))
        return text if abs(ry - y) <= 0.06 else None

    results = []
    for day, l, r in bounds:
        col = img.crop((l // scale_used, 0, r // scale_used, img.height))
        col = col.resize((col.width * 2, col.height * 2), Image.LANCZOS)
        p = os.path.join(WORK, "%s.png" % day)
        col.save(p)

        blocks = [b for b in ocr(p) if header_index(b["text"]) is None]
        blocks.sort(key=lambda b: -b["y"])

        cells, cur, last_y = [], [], None
        for b in blocks:
            if last_y is not None and (last_y - b["y"]) > 0.060:
                cells.append(cur)
                cur = []
            cur.append(b)
            last_y = b["y"]
        if cur:
            cells.append(cur)

        last = None
        for cell in cells:
            text = "".join(b["text"] for b in cell)
            course = cell[0]["text"].strip()
            if not looks_like_course(course):
                # 这一格没有课程名，多半是上一门课的周次/教室被切出来了 → 并回去
                if last is not None:
                    last["raw"] += text
                    last["weeks"] = last["weeks"] or weeks_expr(last["raw"])
                    last["room"] = last["room"] or room_of(last["raw"])
                continue
            y = sum(b["y"] for b in cell) / len(cell)
            item = {
                "day": day,
                "course": course,
                "slot": slot_of(text) or slot_at(y),
                "weeks": weeks_expr(text),
                "room": room_of(text),
                "raw": text,
            }
            results.append(item)
            last = item

    print("\n识别到 %d 门课：\n" % len(results))
    print("%-6s %-8s %-18s %-26s %-14s" % ("星期", "节次", "课程", "周次（代码）", "教室"))
    print("-" * 80)
    for r in results:
        print("%-6s %-8s %-18s %-26s %-14s" % (
            r["day"], r["slot"] or "?", r["course"][:16],
            (r["weeks"] or "⚠️ 未识别")[:24], r["room"] or "?"))

    print("\n# ---- 草稿：粘进 课表生成器.py 的 COURSES，再逐条核对 ----")
    print("COURSES = [")
    for r in results:
        expr = r["weeks"] or "W.range(1, 17)   # ⚠️ 周次识别失败，请手填"
        print('    ("%s", %d, "%s", %s, "%s"),' % (
            r["course"], DAY_NAMES.index(r["day"]),
            r["slot"] or "1-2", expr, r["room"] or ""))
    print("]")
    print("\n⚠️ 识别只是草稿。课程名末字、周次、教室是最容易错的三处，务必对照原图。")
    print("   中间产物：%s" % WORK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
