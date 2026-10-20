#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
课表图片 → COURSES 代码（实验性）

用法:
    python3 tools/识别课表.py 课表截图.png [--force] [--words "高等数学,大学英语"]

做法:
    1. 整图（或表格带）放大后识别一次，找到「星期一…星期日」表头；
    2. 按表头把图切成若干列（表头认不全时按列宽外推补齐），逐列再识别；
    3. 用左侧「1-2 / 3-4」这种节次标签划出行带，把文字块归进格子；
    4. 从每个格子里抠课程名 / 节次 / 周次 / 教室。

质量门槛（见 README「图片要多清晰」）:
    识别后如果字太小（典型是教务系统整屏截图缩过），会直接拒绝并提示重新截图；
    确实想硬跑可以加 --force。

--words: 用逗号分隔的专有词（课名、教室、院系），交给 Vision 做提示，能明显提高识别率。

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
LEFT_COL = 0.16          # 左侧节次列的右边界（归一化 x）
MAX_COLS = 7

WORDS = []               # --words 传入的自定义词表


def ocr(image_path):
    out = subprocess.run(["swift", OCR_SWIFT, image_path] + WORDS,
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


def column_bounds(heads, items):
    """表头 → [(day_index, left, right)]，坐标是归一化的

    表头少了（比如只认出 5 个）也不直接放弃：用相邻表头的中位间距往两边外推，
    补到合理列数为止。这是旧版最脆的地方——少认一个表头整列就错位。
    """
    heads = sorted(heads, key=lambda e: e["x"])
    centers = [(h["x"] + h["w"] / 2, header_index(h["text"])) for h in heads]
    known = [(c, d) for c, d in centers if d is not None]

    if len(known) >= 2:
        gaps = [known[i + 1][0] - known[i][0] for i in range(len(known) - 1)]
        step = statistics.median(gaps)
    elif len(known) == 1:
        step = (1.0 - LEFT_COL) / MAX_COLS
    else:
        return []

    step = max(step, 0.03)

    # 已知表头 → 以「最左已知表头」为锚，按索引差算出每一列的声明位置
    anchor_center, anchor_day = known[0]
    slots = {}
    for c, d in known:
        slots[d] = c
    # 向左补
    i = 1
    lowered = True
    while lowered and anchor_day - i >= 0:
        pos = anchor_center - i * step
        if pos < LEFT_COL:
            break
        slots.setdefault(anchor_day - i, pos)
        i += 1
    # 向右补
    i = 1
    while anchor_day + i < MAX_COLS:
        pos = anchor_center + i * step
        if pos > 1.0 + step / 2:
            break
        slots.setdefault(anchor_day + i, pos)
        i += 1

    known_days = {d for _, d in known}

    # 外推出来的列不一定真的存在（周五后面的周六），拿全文 OCR 结果验一下：
    # 这一列里要是连一个文字块都没有，就说明是多补的，删掉。
    def has_content(day):
        c = slots[day]
        half = step / 2
        for e in items:
            cx = e["x"] + e["w"] / 2
            if abs(cx - c) < half * 0.9:
                return True
        return False

    days = [d for d in sorted(slots) if d in known_days or has_content(d)]
    if not days:
        return []

    bounds = []
    for k, day in enumerate(days):
        c = slots[day]
        left = LEFT_COL if k == 0 else (slots[days[k - 1]] + c) / 2
        right = 1.0 if k == len(days) - 1 else (c + slots[days[k + 1]]) / 2
        bounds.append((DAY_NAMES[day], left, right))
    return bounds


def row_bands(items, img):
    """用左侧「1-2 / 3-4」标签划行带；标签不足时返回 None（退回间距聚类）"""
    rows = []
    for e in items:
        t = e["text"].strip()
        if e["x"] < LEFT_COL and re.fullmatch(r"\d{1,2}(?:[-~－]\d{1,2})?", t):
            rows.append((t, e["y"] + e["h"] / 2))
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: -r[1])
    bands = []
    for i, (text, y) in enumerate(rows):
        top = 1.0 if i == 0 else (rows[i - 1][1] + y) / 2
        bottom = 0.0 if i == len(rows) - 1 else (y + rows[i + 1][1]) / 2
        bands.append((top, bottom, text))
    return bands


def group_by_bands(blocks, bands):
    """把文字块按行带分格；块落在带外就丢掉"""
    cells = [[] for _ in bands]
    for b in blocks:
        y = b["y"] + b["h"] / 2
        for i, (top, bottom, _) in enumerate(bands):
            if bottom <= y <= top:
                cells[i].append(b)
                break
    return cells


def main():
    args = [a for a in sys.argv[1:]]
    force = "--force" in args
    words = []
    if "--words" in args:
        i = args.index("--words")
        if i + 1 < len(args):
            words = [w.strip() for w in args[i + 1].split(",") if w.strip()]
            del args[i:i + 2]
    args = [a for a in args if not a.startswith("--")]
    if not args:
        sys.exit('用法: python3 tools/识别课表.py <课表图片> [--force] [--words "课名1,课名2"]')
    src = args[0]

    global WORDS
    WORDS = words
    if WORDS:
        print("  自定义词表：%s" % ("、".join(WORDS)))

    try:
        from PIL import Image
    except ImportError:
        sys.exit("需要 Pillow：pip3 install pillow")

    os.makedirs(WORK, exist_ok=True)
    img = Image.open(src).convert("RGB")

    base, heads, items, scale_used, _ = find_layout(img, Image)
    if not items:
        print("❌ 整张图一个字都没认出来。可能图太糊、或者根本不是课表。")
        return 1

    if not heads:
        print("❌ 没找到「星期一…星期日」表头。可能不是课表，或版式差异太大。")
        print("   识别到的前几段文字：")
        for e in items[:10]:
            print("     ", e["text"])
        print("   如果是教务系统截图，试试先把课表区域放大再截。")
        return 1

    # 换回「相对原图」的高度：裁剪 + 放大都不该影响判定
    th = median_text_height(items, heads) * (base.height / img.height)
    print("\n  文字高度（归一化）: %.4f   门槛 %.4f" % (th, MIN_TEXT_HEIGHT))
    if th < MIN_TEXT_HEIGHT and not force:
        print("\n❌ 这张图分辨率不足，字太小，识别会大量出错，先不往下跑。")
        print("   建议二选一：")
        print("     1) 重新截图：先把课表区域放大，再截那一块（不要截整屏）")
        print("     2) 有学校导出的 PDF / Excel，改用 tools/读取Excel课表.py")
        print("   确实要硬跑：加 --force，结果务必逐条核对")
        return 2

    bounds = column_bounds(heads, items)
    if not bounds:
        print("❌ 表头信息不够，分不出列。")
        return 1
    if len(bounds) < len(heads):
        pass
    print("  列：%s" % " / ".join(b[0] for b in bounds))

    img = base
    W = img.width * scale_used

    bands = row_bands(items, img)
    if bands:
        print("  行带：%d 条（来自左侧节次标签）" % len(bands))
    else:
        print("  行带：左侧没读到节次标签，退回按文字间距切格")

    results = []
    for day, l, r in bounds:
        col = img.crop((int(l * img.width), 0, int(r * img.width), img.height))
        col = col.resize((col.width * 2, col.height * 2), Image.LANCZOS)
        p = os.path.join(WORK, "%s.png" % day)
        col.save(p)

        blocks = [b for b in ocr(p) if header_index(b["text"]) is None]
        blocks.sort(key=lambda b: -b["y"])

        if bands:
            cells = [c for c in group_by_bands(blocks, bands) if c]
            cells.sort(key=lambda c: -max(b["y"] for b in c))
        else:
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
                "slot": slot_of(text) or slot_at(y, bands),
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


def slot_at(y, bands):
    """块落在哪条行带 → 该带的节次文字"""
    if not bands:
        return None
    for top, bottom, text in bands:
        if bottom <= y <= top:
            return text
    return None


if __name__ == "__main__":
    sys.exit(main())
