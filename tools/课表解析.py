#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
课表解析的公共规则
================================================
图片识别（识别课表.py）和 Excel 读取（读取Excel课表.py）共用这一套规则，
保证两种输入的判定标准完全一致。

只依赖标准库。
"""
import re

DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
DAY_CHARS = "一二三四五六日"


def header_index(text):
    """把「星期一 / 周一 / 周1 / 周一」统一成 0~6；不是表头返回 None"""
    if not text:
        return None
    t = str(text).strip().replace("星期", "周").replace("礼拜", "周")
    m = re.fullmatch(r"周([一二三四五六日天1-7])", t)
    if not m:
        return None
    c = m.group(1)
    if c in "天7":
        return 6
    if c.isdigit():
        return int(c) - 1
    return DAY_CHARS.index(c)


# ---------------------------------------------------------------- 周次
def weeks_expr(text, total=17):
    """周次文字 → W.xxx(...) 表达式

    '1-16周'          → W.range(1, 16)
    '4-6周(双),7-17周' → W.even(4, 6) | W.range(7, 17)
    '单周'             → W.odd(1, total)
    '第5周'            → W.at(5)
    认不出来返回 None（调用方要提示用户手填）
    """
    if not text:
        return None
    t = str(text)

    # 先去掉「(1-2节)」这种节次标记，否则 1-2 会被当成周次
    t = re.sub(r"[(（]?\s*\d{1,2}\s*[-~－]\s*\d{1,2}\s*节\s*[)）]?", " ", t)
    t = re.sub(r"[(（]?\s*\d{1,2}\s*节\s*[)）]?", " ", t)

    # 再去掉教室号，避免「教2-203」被当成周次
    t = re.sub(r"教\s*\d+\s*[-－]\s*\d+", " ", t)
    t = re.sub(r"[教学]\s*\d+\s*楼\s*\d+", " ", t)

    if "单" in t and not re.search(r"\d", t):
        return "W.odd(1, %d)" % total
    if "双" in t and not re.search(r"\d", t):
        return "W.even(1, %d)" % total

    parts = []
    for seg in re.split(r"[,，、;；/]", t):
        seg = seg.strip()
        if not seg:
            continue
        odd, even = "单" in seg, "双" in seg
        m = re.search(r"(\d{1,2})\s*[-~－到至]\s*(\d{1,2})", seg)
        if m:
            a, b = m.group(1), m.group(2)
            if int(a) > int(b):          # 明显是识别错误，不猜
                continue
            if even:
                parts.append("W.even(%s, %s)" % (a, b))
            elif odd:
                parts.append("W.odd(%s, %s)" % (a, b))
            else:
                parts.append("W.range(%s, %s)" % (a, b))
            continue
        m = re.search(r"第?(\d{1,2})周", seg)
        if m:
            parts.append("W.at(%s)" % m.group(1))
    return " | ".join(parts) if parts else None


# ---------------------------------------------------------------- 节次
def slot_of(text):
    """'(1-2节)' → '1-2' ；'(5节)' → '5' ；认不出返回 None"""
    if not text:
        return None
    m = re.search(r"[(（]?\s*(\d{1,2})\s*(?:[-~－]\s*(\d{1,2}))?\s*节", str(text))
    if not m:
        return None
    return m.group(1) if not m.group(2) else "%s-%s" % (m.group(1), m.group(2))


# ---------------------------------------------------------------- 教室
def room_of(text):
    """从一格里抠出上课地点；认不出返回 None"""
    if not text:
        return None
    t = str(text)

    m = re.search(r"场地\s*[:：]\s*([^/／\n]{1,20})", t)
    if m:
        return m.group(1).strip(" ，,。;；")

    if "田径场" in t:
        return "田径场"

    m = re.search(r"(教\d+楼\s*\d{2,4}|学\d+楼\s*\d{2,4}"
                  r"|教\d+[-－]\d{2,4}|实验楼[-－]?\d{0,4})", t)
    if m:
        return m.group(1).replace(" ", "")
    return None


# ---------------------------------------------------------------- 课程名
def looks_like_course(text):
    """判断一段文字像不像课程名（用来过滤页眉、页码、"学号："这类噪声）"""
    if not text:
        return False
    t = str(text).strip()
    if len(t) < 2 or len(t) > 30:
        return False
    if not re.search(r"[一-龥]{2,}", t):
        return False
    if re.fullmatch(r"\d{1,2}", t):
        return False
    for noise in ("上午", "下午", "晚上", "时间段", "节次", "学号", "打印时间", "课表", "备注"):
        if noise in t:
            return False
    if t.count("/") or "：" in t or ":" in t:
        return False
    if re.match(r"^(教|学|实验楼|场地|体育场|田径场)", t):
        return False
    if re.fullmatch(r"[\d\s,.，。:：;；\-－]+", t):
        return False
    return True
