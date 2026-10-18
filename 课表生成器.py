#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
课表 → 日历 (.ics) 生成器
================================================
把一份「不规则」的大学课表展开成标准 iCalendar 文件，
在手机、电脑上点一下「全部添加」，整学期的课就都进日历了。

为什么不用「每周重复事件」：
  课表看起来规律，其实全是例外——单双周、指定周次、节假日停课、
  换季作息调整。用 RRULE 要写一堆 EXDATE 和例外，错一条就会有
  某一节课悄悄消失；直接展开成一次次具体的课，**保证每次都对**。

零依赖：只用 Python 标准库。

用法:
  python3 课表生成器.py               # 生成 .ics
  python3 课表生成器.py --remind 30   # 提前 30 分钟提醒（默认 20）
  python3 课表生成器.py --no-alarm    # 不要提醒
  python3 课表生成器.py --out 我的课表.ics
"""

import argparse
import datetime
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================================
# 1. 学期设置 —— 换学校 / 换学期只改这一块
# ============================================================================
TZID = "Asia/Shanghai"

START = datetime.date(2026, 9, 7)          # 第 1 周的周一
WEEKS = 17                                  # 一共有几周
WINTER_FROM = datetime.date(2026, 10, 1)    # 从这天起换成冬季作息

CAL_NAME = "我的课表"
CAL_DESC = "2026-2027 学年第一学期"


# ============================================================================
# 2. 节假日（这些日子不排课）
# ============================================================================
def _dates(year, month, d1, d2):
    return {datetime.date(year, month, d) for d in range(d1, d2 + 1)}


HOLIDAY = set()
HOLIDAY |= _dates(2026, 9, 25, 27)     # 中秋
HOLIDAY |= _dates(2026, 10, 1, 7)      # 国庆
HOLIDAY |= _dates(2027, 1, 1, 1)       # 元旦
# 需要的话继续加，比如校运会、调休补班


# ============================================================================
# 3. 作息时间：第 N 节课的「开始 / 结束」，写成从 0 点起的分钟数
#    8:00 = 480，13:30 = 810
# ============================================================================
SUMMER = {
    1: (480, 525), 2: (530, 575),                       #  8:00-8:45   8:50-9:35
    3: (590, 635), 4: (640, 685), 5: (690, 735),        #  9:50-10:35 10:40-11:25 11:30-12:15
    6: (840, 885), 7: (890, 935),                       # 14:00-14:45 14:50-15:35
    8: (950, 995), 9: (1000, 1045),                     # 15:50-16:35 16:40-17:25
    10: (1140, 1185), 11: (1190, 1235), 12: (1240, 1285),
}

WINTER = {
    1: (480, 525), 2: (530, 575),
    3: (590, 635), 4: (640, 685), 5: (690, 735),        # 上午和夏季一样
    6: (810, 855), 7: (860, 905),                       # 13:30-14:15 14:20-15:05
    8: (920, 965), 9: (970, 1015),                      # 15:20-16:05 16:10-16:55
    10: (1110, 1155), 11: (1160, 1205), 12: (1210, 1255),
}

# 一节课的「行」→ 实际用到第几节到第几节
SLOT_PERIODS = {
    "1-2": (1, 2),
    "3-4": (3, 4),
    "5": (5, 5),
    "6-7": (6, 7),
    "8-9": (8, 9),
    "10-12": (10, 12),
    "6-8": (6, 8),      # 三节连堂的情况，按需增加
}

SLOT_LABEL = {
    "1-2": "第1-2节",
    "3-4": "第3-4节",
    "5": "第5节",
    "6-7": "第6-7节",
    "8-9": "第8-9节",
    "10-12": "第10-12节",
    "6-8": "第6-8节",
}


# ============================================================================
# 4. 周次助手：写起来像人话
#    W.range(1, 16)  第 1 到 16 周
#    W.even(4, 12)   4~12 周里的双周
#    W.odd(3, 11)    3~11 周里的单周
#    可以直接用 | 合并，比如 W.range(1, 5) | W.odd(9, 15)
# ============================================================================
class W:
    @staticmethod
    def range(a, b):
        """连续周次：第 a~b 周"""
        return set(range(a, b + 1))

    @staticmethod
    def even(a, b):
        """双周"""
        return {w for w in range(a, b + 1) if w % 2 == 0}

    @staticmethod
    def odd(a, b):
        """单周"""
        return {w for w in range(a, b + 1) if w % 2 == 1}

    @staticmethod
    def at(*weeks):
        """想上哪几周就写哪几周，完全不规律的间隔也能写
        W.at(1, 5, 9, 14)  →  第 1、5、9、14 周
        """
        return set(weeks)

    @staticmethod
    def step(a, b, n):
        """每隔 n 周上一次：W.step(1, 16, 3) → 1、4、7、10、13、16"""
        return {w for w in range(a, b + 1) if (w - a) % n == 0}


# ============================================================================
# 5. 课程表  —— 换成你自己的就行
#    格式：(课程名, 星期(0=周一), 节次行, 周次, 教室)
# ============================================================================
COURSES = [
    ("高等数学",       0, "1-2", W.even(4, 6) | W.range(7, 16), "教2-203"),
    ("高等数学",       2, "3-4", W.even(4, 6) | W.range(7, 16), "教2-203"),

    ("大学英语",       1, "1-2", W.range(4, 16),                 "教1-408"),
    ("大学英语",       3, "8-9", {16},                            "教1-408"),

    ("程序设计基础",   2, "1-2", W.even(4, 6) | W.range(7, 15),  "实验楼-207"),
    ("程序设计基础",   4, "6-7", W.range(8, 13),                 "实验楼-207"),

    ("体育",           2, "6-7", W.range(4, 16),                 "田径场"),
    ("大学生写作",     0, "6-8", W.range(7, 14),                 "教1-310"),

    # 只上一次的课，直接写一个周次集合 {5}
    ("安全教育讲座",   4, "3-4", {5},                            "报告厅"),
]

# ============================================================================
# 6. 临时补课 / 调休挪课 —— 日期不跟着周次走的时候用这个
#    格式：(课程名, 具体日期, 节次行, 教室)
# ============================================================================
EXTRA = [
    # ("高等数学", datetime.date(2026, 10, 10), "1-2", "教2-203"),   # 周六补周一
]

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ============================================================================
# 展开成一次次具体的课
# ============================================================================
def _slot_times(d, slot):
    """某天某节次的开始 / 结束分钟（自动选夏/冬作息）"""
    table = SUMMER if d < WINTER_FROM else WINTER
    first, last = SLOT_PERIODS[slot]
    return table[first][0], table[last][1]


def week_of(d):
    """某个日期属于第几周"""
    return (d - START).days // 7 + 1


def expand():
    """返回 [(日期, 开始分钟, 结束分钟, 课程, 教室, 周次, 节次行)]"""
    out = []

    # ① 按周次展开的常规课
    for course, day, slot, weeks, room in COURSES:
        if slot not in SLOT_PERIODS:
            raise ValueError("未知节次写法：%s（可选：%s）"
                             % (slot, "/".join(SLOT_PERIODS)))
        for w in sorted(weeks):
            if not 1 <= w <= WEEKS:
                continue
            d = START + datetime.timedelta(days=7 * (w - 1) + day)
            if d in HOLIDAY:
                continue
            s, e = _slot_times(d, slot)
            out.append((d, s, e, course, room, w, slot))

    # ② 指定日期的补课
    for course, d, slot, room in EXTRA:
        if slot not in SLOT_PERIODS:
            raise ValueError("补课用了未知节次写法：%s" % slot)
        s, e = _slot_times(d, slot)
        out.append((d, s, e, course, room, week_of(d), slot))

    out.sort(key=lambda x: (x[0], x[1]))
    return out


def find_conflicts(events):
    """找出同一天时间重叠的课，返回 [(日期, 课程A, 课程B)]"""
    bad, by_day = [], {}
    for e in events:
        by_day.setdefault(e[0], []).append(e)
    for d, items in by_day.items():
        items.sort(key=lambda x: x[1])
        for i in range(len(items) - 1):
            a, b = items[i], items[i + 1]
            if b[1] < a[2]:                     # 下一节开始早于上一节结束
                bad.append((d, a[3], b[3]))
    return bad


# ============================================================================
# iCalendar 输出
# ============================================================================
def esc(text):
    """RFC 5545 规定要转义的四个字符"""
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line):
    """按 75 字节折行，且不会把一个汉字劈成两半"""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 72:
            parts.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return "\r\n ".join(parts)


def hm(minutes):
    return "%02d%02d00" % (minutes // 60, minutes % 60)


def build(remind=20):
    stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//timetable-to-ics//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + esc(CAL_NAME),
        "X-WR-CALDESC:" + esc(CAL_DESC),
        "X-WR-TIMEZONE:" + TZID,
        "BEGIN:VTIMEZONE",
        "TZID:" + TZID,
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    events = expand()
    for d, start, end, course, room, week, slot in events:
        lines += [
            "BEGIN:VEVENT",
            "UID:%s-%s-%s@timetable" % (d.strftime("%Y%m%d"), slot, course),
            "DTSTAMP:" + stamp,
            "DTSTART;TZID=%s:%04d%02d%02dT%s" % (TZID, d.year, d.month, d.day, hm(start)),
            "DTEND;TZID=%s:%04d%02d%02dT%s" % (TZID, d.year, d.month, d.day, hm(end)),
            "SUMMARY:" + esc(course),
            "LOCATION:" + esc(room),
            "DESCRIPTION:" + esc("第%d周 %s %s" % (week, WEEKDAYS[d.weekday()], SLOT_LABEL[slot])),
            "TRANSP:OPAQUE",
            "STATUS:CONFIRMED",
            "SEQUENCE:0",
        ]
        if remind > 0:
            lines += [
                "BEGIN:VALARM",
                "TRIGGER:-PT%dM" % remind,
                "ACTION:DISPLAY",
                "DESCRIPTION:" + esc(course + " 快上课了"),
                "END:VALARM",
            ]
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in lines) + "\r\n", events


# ============================================================================
def main():
    ap = argparse.ArgumentParser(description="把课表生成标准日历文件（.ics）")
    ap.add_argument("--remind", type=int, default=20, help="提前几分钟提醒，0 = 不提醒")
    ap.add_argument("--no-alarm", action="store_true", help="完全不设置提醒")
    ap.add_argument("--out", default=os.path.join(BASE_DIR, "课表.ics"), help="输出文件名")
    args = ap.parse_args()

    remind = 0 if args.no_alarm else args.remind
    ics, events = build(remind=remind)

    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write(ics)

    print()
    print("✅ 已生成：%s" % args.out)
    print("   共 %d 节课，%.1f KB" % (len(events), os.path.getsize(args.out) / 1024.0))
    if events:
        print("   范围：%s ~ %s" % (events[0][0].isoformat(), events[-1][0].isoformat()))
    if remind > 0:
        print("   提醒：每节课提前 %d 分钟" % remind)

    conflicts = find_conflicts(events)
    if conflicts:
        print()
        print("   ⚠️  发现 %d 处时间重叠（多半是周次写重了，或调休没处理）：" % len(conflicts))
        for d, a, b in conflicts[:10]:
            print("      %s   %s ↔ %s" % (d.isoformat(), a, b))
        if len(conflicts) > 10:
            print("      …… 还有 %d 处" % (len(conflicts) - 10))

    print()
    print("   在手机上打开这个文件 →「全部添加」")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
