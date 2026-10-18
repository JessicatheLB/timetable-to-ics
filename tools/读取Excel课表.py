#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel 课表 → COURSES 代码

用法:
    python3 tools/读取Excel课表.py 课表.xlsx [--sheet 1]

为什么要用 Excel 而不是截图:
    截图要靠 OCR 猜，Excel 里的文字是**原样存的**，准确率 100%。
    能从教务系统导出 Excel / CSV 的，优先走这条路。

支持:
    - .xlsx（Excel 2007+）：纯标准库解析，不装 openpyxl / pandas
    - 合并单元格（比如「上午」跨三行、一节大课跨两个节次）
    - 一个格子写多门课（用换行分隔）
    - 表头写「星期一」或「周一」都认

不支持:
    - .xls（老式二进制格式），请先用 Excel/WPS 另存为 .xlsx
"""
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from 课表解析 import (DAY_NAMES, header_index, weeks_expr,   # noqa: E402
                      slot_of, room_of, looks_like_course)

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


# ---------------------------------------------------------------- xlsx 读取
def col_index(ref):
    """'AB12' → 27（0 起）"""
    letters = re.match(r"([A-Z]+)", ref)
    if not letters:
        return None
    n = 0
    for ch in letters.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def row_index(ref):
    m = re.search(r"(\d+)", ref)
    return int(m.group(1)) - 1 if m else None


def load_sheet(path, sheet_no=1):
    """返回 {(row, col): text}"""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
        if not names:
            sys.exit("这个 xlsx 里找不到工作表。")
        names.sort()
        target = names[min(sheet_no, len(names)) - 1]

        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))

        root = ET.fromstring(z.read(target))

    grid = {}
    for c in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
        ref = c.get("r")
        if not ref:
            continue
        r, col = row_index(ref), col_index(ref)
        if r is None or col is None:
            continue
        t = c.get("t")
        text = ""
        if t == "inlineStr":
            text = "".join(x.text or "" for x in c.iter(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
        else:
            v = c.find("m:v", NS)
            if v is not None and v.text is not None:
                text = shared[int(v.text)] if t == "s" else v.text
        if text:
            grid[(r, col)] = text.strip()

    # 合并单元格：把左上角的值铺满整个区域
    for mc in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}mergeCell"):
        ref = mc.get("ref") or ""
        if ":" not in ref:
            continue
        a, b = ref.split(":")
        r1, c1 = row_index(a), col_index(a)
        r2, c2 = row_index(b), col_index(b)
        if None in (r1, c1, r2, c2):
            continue
        val = grid.get((r1, c1))
        if not val:
            continue
        for rr in range(r1, r2 + 1):
            for cc in range(c1, c2 + 1):
                grid.setdefault((rr, cc), val)

    return grid


# ---------------------------------------------------------------- 解析
def split_courses(text):
    """一个格子里可能有多门课（用换行分隔），拆成 [(课程名, 详情)]"""
    out = []
    cur = None
    for raw in str(text).split("\n"):
        line = raw.strip()
        if not line:
            continue
        if looks_like_course(line) and not line.startswith(("(", "（")):
            cur = [line, ""]
            out.append(cur)
        elif cur is not None:
            cur[1] += line
    return [(n, d) for n, d in out]


def main():
    args = sys.argv[1:]
    sheet_no = 1
    if "--sheet" in args:
        i = args.index("--sheet")
        sheet_no = int(args[i + 1])
        del args[i:i + 2]
    if not args:
        sys.exit("用法: python3 tools/读取Excel课表.py 课表.xlsx [--sheet 1]")

    path = args[0]
    if not os.path.exists(path):
        sys.exit("找不到文件：%s" % path)
    if path.lower().endswith(".xls"):
        sys.exit("不支持老的 .xls，请用 Excel/WPS 另存为 .xlsx 再来。")

    grid = load_sheet(path, sheet_no)
    if not grid:
        sys.exit("工作表是空的。")

    # 找表头行
    header_row, cols = None, {}
    for (r, c), text in grid.items():
        idx = header_index(text)
        if idx is not None:
            if header_row is None:
                header_row = r
            if r == header_row:
                cols[c] = idx
    if not cols or len(cols) < 3:
        sys.exit("没找到「星期一…星期日」表头，确认一下是不是课表工作表。")

    results = []
    for (r, c), text in sorted(grid.items()):
        if r <= header_row or c not in cols:
            continue
        for course, detail in split_courses(text):
            results.append({
                "day": DAY_NAMES[cols[c]],
                "course": course,
                "slot": slot_of(detail),
                "weeks": weeks_expr(detail),
                "room": room_of(detail),
                "raw": detail,
            })

    # 同一格里重复的（合并单元格铺开造成）去重
    seen, uniq = set(), []
    for r in results:
        key = (r["day"], r["course"], r["slot"], r["weeks"], r["room"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)

    print("\n读取到 %d 门课：\n" % len(uniq))
    print("%-6s %-8s %-18s %-26s %-14s" % ("星期", "节次", "课程", "周次（代码）", "教室"))
    print("-" * 80)
    for r in uniq:
        print("%-6s %-8s %-18s %-26s %-14s" % (
            r["day"], r["slot"] or "?", r["course"][:16],
            (r["weeks"] or "⚠️ 未识别")[:24], r["room"] or "?"))

    print("\n# ---- 粘进 课表生成器.py 的 COURSES ----")
    print("COURSES = [")
    for r in uniq:
        expr = r["weeks"] or "W.range(1, 17)   # ⚠️ 周次没解析出来，请手填"
        print('    ("%s", %d, "%s", %s, "%s"),' % (
            r["course"], DAY_NAMES.index(r["day"]),
            r["slot"] or "1-2", expr, r["room"] or ""))
    print("]")
    print("\n提示：Excel 里读的是原文，一般比截图准；但周次写法和教室位置各家不同，"
          "\n     第一次用请对照原表扫一眼。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
