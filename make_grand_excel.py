# -*- coding: utf-8 -*-
"""Grand Ultimate Excel Workbook Generator for Shiying Project."""
import os
import sys
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from append_mgr_ui import MGR_AND_UI_FEATURES
from append_api_dsk_sec import API_DSK_SEC_FEATURES
from platform_and_api_data import PLATFORM_MATRIX, API_ENDPOINTS
from detailed_logic_data import DETAILED_LOGIC_BRANCHES
from super_detailed_logic_data import MICRO_ENGINE_AND_OS_LOGIC
from error_decision_tree_data import SITE_ISSUE_TREE
from ui_state_machine_data import UI_STATE_MACHINE

# 1. Load Part 1 features
ns = {}
with open('features_data.py', 'r', encoding='utf-8') as f:
    text = f.read().split('ALL_FEATURES.extend')[0]
exec(text, ns)
F_BASE_52 = ns['ALL_FEATURES'][:52]

BASE_146 = F_BASE_52 + MGR_AND_UI_FEATURES + API_DSK_SEC_FEATURES
print(f"Base features: {len(BASE_146)}")
print(f"Detailed logic branches: {len(DETAILED_LOGIC_BRANCHES)}")
print(f"Micro engine & OS logic: {len(MICRO_ENGINE_AND_OS_LOGIC)}")

COMBINED_DETAILS = []

# Adapt 146 base features
for item in BASE_146:
    COMBINED_DETAILS.append({
        "id": item["id"],
        "category": item["category"],
        "subsystem": item["subsystem"],
        "name": item["name"],
        "condition": item.get("trigger", "常规用户操作或后台调度触发"),
        "logic": item["desc"],
        "code": item["code"],
        "status": item["status"],
        "rules": item["rules"]
    })

# Append 114 fine-grained logic branches
for item in DETAILED_LOGIC_BRANCHES:
    cat = item["category"]
    if cat == "平台接入与媒体逆向":
        cat = "平台接入与媒体解析"
    COMBINED_DETAILS.append({
        "id": item["id"],
        "category": cat,
        "subsystem": item["subsystem"],
        "name": item["name"],
        "condition": item["condition"],
        "logic": item["logic"],
        "code": item["code"],
        "status": item["result"],
        "rules": item["safety"]
    })

# Append 19 micro-engine and OS logic branches
for item in MICRO_ENGINE_AND_OS_LOGIC:
    COMBINED_DETAILS.append({
        "id": item["id"],
        "category": item["category"],
        "subsystem": item["subsystem"],
        "name": item["name"],
        "condition": item["condition"],
        "logic": item["logic"],
        "code": item["code"],
        "status": item["result"],
        "rules": item["safety"]
    })

TOTAL_ROWS = len(COMBINED_DETAILS)
print(f"Total grand unified logic rows: {TOTAL_ROWS}")
assert TOTAL_ROWS == 146 + 114 + len(MICRO_ENGINE_AND_OS_LOGIC)

OUTPUT_FILE = "拾影项目全功能矩阵清单.xlsx"
wb = openpyxl.Workbook()
wb.remove(wb.active)

# Palette
COLOR_NAVY_DARK = "1B365D"
COLOR_NAVY_LIGHT = "E8EEF5"
COLOR_SLATE = "4A6572"
COLOR_ZEBRA = "F7F9FB"
COLOR_WHITE = "FFFFFF"
COLOR_BORDER = "D1D5DB"

COLOR_READY_BG = "E6F4EA"
COLOR_READY_FG = "137333"
COLOR_COOKIE_BG = "E8F0FE"
COLOR_COOKIE_FG = "1A73E8"
COLOR_EXP_BG = "FEF7E0"
COLOR_EXP_FG = "B06000"
COLOR_LIMIT_BG = "FCE8E6"
COLOR_LIMIT_FG = "C5221F"

font_family = "Microsoft YaHei"

font_title = Font(name=font_family, size=16, bold=True, color=COLOR_NAVY_DARK)
font_subtitle = Font(name=font_family, size=10, italic=True, color="555555")
font_table_header = Font(name=font_family, size=10, bold=True, color=COLOR_WHITE)
font_sub_header = Font(name=font_family, size=10, bold=True, color=COLOR_NAVY_DARK)
font_regular = Font(name=font_family, size=9, color="222222")
font_bold = Font(name=font_family, size=9, bold=True, color="222222")
font_code = Font(name="Consolas", size=9, color="2A4365")
font_kpi_num = Font(name=font_family, size=20, bold=True, color=COLOR_NAVY_DARK)
font_kpi_label = Font(name=font_family, size=9, bold=True, color="555555")

fill_header_navy = PatternFill(start_color=COLOR_NAVY_DARK, end_color=COLOR_NAVY_DARK, fill_type="solid")
fill_header_slate = PatternFill(start_color=COLOR_SLATE, end_color=COLOR_SLATE, fill_type="solid")
fill_sub_header = PatternFill(start_color=COLOR_NAVY_LIGHT, end_color=COLOR_NAVY_LIGHT, fill_type="solid")
fill_zebra = PatternFill(start_color=COLOR_ZEBRA, end_color=COLOR_ZEBRA, fill_type="solid")
fill_white = PatternFill(start_color=COLOR_WHITE, end_color=COLOR_WHITE, fill_type="solid")

fill_ready = PatternFill(start_color=COLOR_READY_BG, end_color=COLOR_READY_BG, fill_type="solid")
font_ready = Font(name=font_family, size=9, bold=True, color=COLOR_READY_FG)

fill_cookie = PatternFill(start_color=COLOR_COOKIE_BG, end_color=COLOR_COOKIE_BG, fill_type="solid")
font_cookie = Font(name=font_family, size=9, bold=True, color=COLOR_COOKIE_FG)

fill_exp = PatternFill(start_color=COLOR_EXP_BG, end_color=COLOR_EXP_BG, fill_type="solid")
font_exp = Font(name=font_family, size=9, bold=True, color=COLOR_EXP_FG)

fill_limit = PatternFill(start_color=COLOR_LIMIT_BG, end_color=COLOR_LIMIT_BG, fill_type="solid")
font_limit = Font(name=font_family, size=9, bold=True, color=COLOR_LIMIT_FG)

thin_side = Side(border_style="thin", color=COLOR_BORDER)
border_all_thin = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)
align_left_top = Alignment(horizontal="left", vertical="top", wrap_text=True)
align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
align_center_top = Alignment(horizontal="center", vertical="top", wrap_text=True)


# =============================================================================
# SHEET 1: 系统概览与指标看板 (System Overview & KPIs)
# =============================================================================
ws1 = wb.create_sheet(title="系统概览与指标看板")
ws1.views.sheetView[0].showGridLines = True

ws1.merge_cells("A1:I1")
c = ws1["A1"]
c.value = "拾影（Shiying）本地多媒体采集系统 · 全功能全景与极端边界逻辑全景矩阵"
c.font = font_title
c.alignment = Alignment(horizontal="left", vertical="center")
ws1.row_dimensions[1].height = 40

ws1.merge_cells("A2:I2")
c = ws1["A2"]
c.value = "架构版本：v0.3.0 工业级生产基准 | 全系统解耦：7 大子系统、279 项微观分支逻辑、15 类风控决策树、33 个 API 契约字典"
c.font = font_subtitle
c.alignment = Alignment(horizontal="left", vertical="center")
ws1.row_dimensions[2].height = 20

kpis = [
    ("B4", "C4", "B5", "C5", f"=COUNTA('全系统功能与极端分支明细'!A2:A{TOTAL_ROWS+1})", "全系统功能与极端逻辑总数 (项)"),
    ("D4", "E4", "D5", "E5", "6 + 1", "覆盖平台与作品形态 (主流+精选)"),
    ("F4", "G4", "F5", "G5", f"=COUNTA('异常风控与降级决策树'!A2:A{len(SITE_ISSUE_TREE)+1})", "错误风控诊断分类体系 (类)"),
    ("H4", "I4", "H5", "I5", f"=COUNTA('RESTful API 契约字典'!A2:A{len(API_ENDPOINTS)+1})", "RESTful API / SSE 端点 (个)"),
]

for val_tl, val_br, lbl_tl, lbl_br, val, lbl in kpis:
    ws1.merge_cells(f"{val_tl}:{val_br}")
    c = ws1[val_tl]
    c.value = val
    c.font = font_kpi_num
    c.alignment = align_center
    c.fill = fill_sub_header

    ws1.merge_cells(f"{lbl_tl}:{lbl_br}")
    c_lbl = ws1[lbl_tl]
    c_lbl.value = lbl
    c_lbl.font = font_kpi_label
    c_lbl.alignment = align_center
    c_lbl.fill = fill_zebra

    for r in range(int(val_tl[1]), int(lbl_br[1]) + 1):
        for col_char in [val_tl[0], val_br[0]]:
            ws1[f"{col_char}{r}"].border = border_all_thin

ws1.row_dimensions[4].height = 32
ws1.row_dimensions[5].height = 20

ws1.cell(row=7, column=1, value="一、7大架构子系统细分逻辑分布统计").font = Font(name=font_family, size=11, bold=True, color=COLOR_NAVY_DARK)
ws1.row_dimensions[7].height = 25

headers_s1 = ["模块编号", "一级架构模块", "核心职责与覆盖深度", "覆盖逻辑项数", "状态", "明细工作表"]
ws1.row_dimensions[8].height = 26
for col_idx, h in enumerate(headers_s1, start=1):
    c = ws1.cell(row=8, column=col_idx, value=h)
    c.font = font_table_header
    c.fill = fill_header_navy
    c.alignment = align_center
    c.border = border_all_thin

module_summary_data = [
    ("MOD-01", "平台接入与媒体解析", "涵盖抖音双源探测、交错路由、FFprobe流复核、小红书direct CDN/originVideoKey、快手切片、B站无损Remux、YouTube Deno挑战、视频号ISAAC64解密等", "=COUNTIF('全系统功能与极端分支明细'!B:B, \"平台接入与媒体解析\")", "已全量验证", "详见 Tab2 / Tab3"),
    ("MOD-02", "下载核心引擎与媒体管线", "涵盖分段隔离缓存、断点续传416重置、二进制魔数嗅探、FFmpeg逐像素解码校验、原子发布、重名加(2)递增、首尾哈希快速比对、实况成对独占锁等", "=COUNTIF('全系统功能与极端分支明细'!B:B, \"下载核心引擎与媒体管线\")", "已全量验证", "详见 Tab2 / Tab7"),
    ("MOD-03", "任务调度与生命周期管理", "涵盖全局并发槽位、10种状态机变迁、暂停保留part缓存、恢复续传、单项重试、全部失败一键重试、物理文件保留删除、状态文件fsync原子刷盘等", "=COUNTIF('全系统功能与极端分支明细'!B:B, \"任务调度与生命周期管理\")", "已全量验证", "详见 Tab2"),
    ("MOD-04", "前端交互与现代UI系统", "涵盖智能文本提取、解析预览弹窗勾选、重复任务拦截弹窗、7大平台快捷过滤、分类Tab筛选、搜索框模糊匹配、内置画廊/播放器、设置抽屉、心跳断网重连等", "=COUNTIF('全系统功能与极端分支明细'!B:B, \"前端交互与现代UI系统\")", "已全量验证", "详见 Tab2 / Tab6"),
    ("MOD-05", "RESTful API与网络通信", "涵盖33个端点契约、TrustedHost防DNS重绑定、local_mutations_only防跨站CSRF、静态资源防脏读、_resolve_within路径穿越拦截、config损坏自愈、SSE长连接等", "=COUNTIF('全系统功能与极端分支明细'!B:B, \"RESTful API与网络通信\")", "已全量验证", "详见 Tab2 / Tab5"),
    ("MOD-06", "桌面客户端集成与系统守护", "涵盖Win32 Job Object进程树级联终止、8766端口智能复用、Windows命名互斥体、Python.NET COM资源管理器置顶激活、OPENABLE_MEDIA_SUFFIXES白名单防投毒、环境探针等", "=COUNTIF('全系统功能与极端分支明细'!B:B, \"桌面客户端集成与系统守护\")", "已全量验证", "详见 Tab2 / Tab7"),
    ("MOD-07", "安全防护、反风控与故障诊断", "涵盖15大类SiteIssueCode判定、滑块风控Chrome唤醒、Cookie读取防锁、域名逐跳白名单拦截、敏感Token/Cookie零泄露脱敏、元宝DPAPI硬件级加密等", "=COUNTIF('全系统功能与极端分支明细'!B:B, \"安全防护、反风控与故障诊断\")", "已全量验证", "详见 Tab2 / Tab4 / Tab7"),
]

for row_idx, row_data in enumerate(module_summary_data, start=9):
    ws1.row_dimensions[row_idx].height = 24
    fill_row = fill_zebra if row_idx % 2 == 0 else fill_white
    for col_idx, val in enumerate(row_data, start=1):
        c = ws1.cell(row=row_idx, column=col_idx, value=val)
        c.font = font_bold if col_idx in (1, 2) else font_regular
        c.fill = fill_row
        c.border = border_all_thin
        c.alignment = align_center if col_idx in (1, 4, 5) else align_left

row_total = 9 + len(module_summary_data)
ws1.row_dimensions[row_total].height = 25
ws1.cell(row=row_total, column=1, value="合计").alignment = align_center
ws1.cell(row=row_total, column=2, value="7 大核心子系统").font = font_bold
ws1.cell(row=row_total, column=3, value="覆盖整个项目全链路功能与极端分支逻辑，无任何死角遗漏").font = font_regular
ws1.cell(row=row_total, column=4, value="=SUM(D9:D15)").font = font_bold
ws1.cell(row=row_total, column=4).alignment = align_center
ws1.cell(row=row_total, column=5, value="100% 覆盖").font = font_ready
ws1.cell(row=row_total, column=5).alignment = align_center
ws1.cell(row=row_total, column=6, value=f"{TOTAL_ROWS} 项全景细则").font = font_bold
ws1.cell(row=row_total, column=6).alignment = align_center

for col_idx in range(1, 7):
    ws1.cell(row=row_total, column=col_idx).fill = fill_sub_header
    ws1.cell(row=row_total, column=col_idx).border = border_all_thin

ws1.column_dimensions["A"].width = 15
ws1.column_dimensions["B"].width = 24
ws1.column_dimensions["C"].width = 44
ws1.column_dimensions["D"].width = 22
ws1.column_dimensions["E"].width = 20
ws1.column_dimensions["F"].width = 22


# =============================================================================
# SHEET 2: 全系统功能与极端分支明细 (All 279 Features & Boundary Branches)
# =============================================================================
ws2 = wb.create_sheet(title="全系统功能与极端分支明细")
ws2.views.sheetView[0].showGridLines = True
ws2.freeze_panes = "A2"
ws2.row_dimensions[1].height = 28

headers_f = [
    "编号", "一级架构模块", "二级子系统 / 业务场景", "功能 / 逻辑名称",
    "前置触发条件 / 触发输入", "系统核心执行逻辑与分支处理",
    "核心实现代码文件与函数", "输出结果 / 状态码 / 界面表现", "安全防御 / 容灾约束机制"
]

for col_idx, h in enumerate(headers_f, start=1):
    c = ws2.cell(row=1, column=col_idx, value=h)
    c.font = font_table_header
    c.fill = fill_header_navy
    c.alignment = align_center
    c.border = border_all_thin

alignments_f = {
    1: align_center_top, 2: align_center_top, 3: align_left_top,
    4: align_left_top, 5: align_left_top, 6: align_left_top,
    7: align_left_top, 8: align_left_top, 9: align_left_top
}

for r_idx, item in enumerate(COMBINED_DETAILS, start=2):
    ws2.row_dimensions[r_idx].height = 28
    fill_row = fill_zebra if r_idx % 2 == 0 else fill_white
    row_values = [
        item["id"], item["category"], item["subsystem"], item["name"],
        item["condition"], item["logic"], item["code"], item["status"], item["rules"]
    ]
    for c_idx, val in enumerate(row_values, start=1):
        c = ws2.cell(row=r_idx, column=c_idx, value=val)
        c.fill = fill_row
        c.border = border_all_thin
        c.alignment = alignments_f.get(c_idx, align_left_top)

        if c_idx == 1:
            c.font = font_bold
        elif c_idx == 7:
            c.font = font_code
        else:
            c.font = font_regular

        if c_idx == 8:
            st = str(val)
            if "生产级就绪" in st or "已验证" in st or "成功" in st:
                c.fill = fill_ready
                c.font = font_ready
            elif "需" in st or "Cookie" in st or "登录" in st or "验证" in st:
                c.fill = fill_cookie
                c.font = font_cookie
            elif "实验性" in st or "重试" in st or "自愈" in st or "升级" in st:
                c.fill = fill_exp
                c.font = font_exp
            elif "抛出" in st or "阻断" in st or "拒绝" in st or "拦截" in st:
                c.fill = fill_limit
                c.font = font_limit

last_col_f = get_column_letter(len(headers_f))
ws2.auto_filter.ref = f"A1:{last_col_f}{TOTAL_ROWS + 1}"

ws2.column_dimensions["A"].width = 16
ws2.column_dimensions["B"].width = 22
ws2.column_dimensions["C"].width = 22
ws2.column_dimensions["D"].width = 28
ws2.column_dimensions["E"].width = 36
ws2.column_dimensions["F"].width = 46
ws2.column_dimensions["G"].width = 34
ws2.column_dimensions["H"].width = 28
ws2.column_dimensions["I"].width = 34


# =============================================================================
# SHEET 3: 平台逆向与媒体极限规则矩阵 (Platform Extreme Rules)
# =============================================================================
ws3 = wb.create_sheet(title="平台逆向与媒体极限规则矩阵")
ws3.views.sheetView[0].showGridLines = True
ws3.freeze_panes = "A2"
ws3.row_dimensions[1].height = 28

headers_p = [
    "平台名称", "支持 URL 模式与样本规则", "支持媒体类型全览",
    "清晰度与最高画质选择机制", "媒体解密 / 合成与流校验", "目录与文件命名归档规范",
    "登录态策略与凭证来源", "安全校验与防串号防投毒机制", "异常处理与风控引导策略", "测试验收成熟度"
]

for col_idx, h in enumerate(headers_p, start=1):
    c = ws3.cell(row=1, column=col_idx, value=h)
    c.font = font_table_header
    c.fill = fill_header_navy
    c.alignment = align_center
    c.border = border_all_thin

alignments_p = {
    1: align_center_top, 2: align_left_top, 3: align_left_top,
    4: align_left_top, 5: align_left_top, 6: align_left_top,
    7: align_left_top, 8: align_left_top, 9: align_left_top, 10: align_center_top
}

for r_idx, item in enumerate(PLATFORM_MATRIX, start=2):
    ws3.row_dimensions[r_idx].height = 44
    fill_row = fill_zebra if r_idx % 2 == 0 else fill_white
    row_values = [
        item["platform"], item["urls"], item["media_types"], item["quality_rule"],
        item["crypto_remux"], item["storage_rule"], item["cookie_policy"],
        item["security_rule"], item["risk_handling"], item["maturity"]
    ]
    for c_idx, val in enumerate(row_values, start=1):
        c = ws3.cell(row=r_idx, column=c_idx, value=val)
        c.fill = fill_row
        c.border = border_all_thin
        c.alignment = alignments_p.get(c_idx, align_left_top)
        c.font = font_bold if c_idx == 1 else font_regular
        if c_idx == 10:
            c.fill = fill_ready
            c.font = font_ready

last_col_p = get_column_letter(len(headers_p))
ws3.auto_filter.ref = f"A1:{last_col_p}{len(PLATFORM_MATRIX) + 1}"

ws3.column_dimensions["A"].width = 18
ws3.column_dimensions["B"].width = 30
ws3.column_dimensions["C"].width = 28
ws3.column_dimensions["D"].width = 36
ws3.column_dimensions["E"].width = 32
ws3.column_dimensions["F"].width = 32
ws3.column_dimensions["G"].width = 30
ws3.column_dimensions["H"].width = 32
ws3.column_dimensions["I"].width = 30
ws3.column_dimensions["J"].width = 22


# =============================================================================
# SHEET 4: 异常风控与降级决策树 (Error Taxonomy & Fallback Tree)
# =============================================================================
ws4 = wb.create_sheet(title="异常风控与降级决策树")
ws4.views.sheetView[0].showGridLines = True
ws4.freeze_panes = "A2"
ws4.row_dimensions[1].height = 28

headers_err = [
    "分类代码 (SiteIssueCode)", "错误类型中文名称", "触发前置条件与业务场景",
    "匹配正则表达式 / 特征关键词", "受影响重点平台", "系统内部执行动作与降级策略",
    "前端界面状态与告警呈现", "用户自助解决手册与排查步骤"
]

for col_idx, h in enumerate(headers_err, start=1):
    c = ws4.cell(row=1, column=col_idx, value=h)
    c.font = font_table_header
    c.fill = fill_header_navy
    c.alignment = align_center
    c.border = border_all_thin

alignments_err = {
    1: align_center_top, 2: align_left_top, 3: align_left_top,
    4: align_left_top, 5: align_left_top, 6: align_left_top,
    7: align_left_top, 8: align_left_top
}

for r_idx, item in enumerate(SITE_ISSUE_TREE, start=2):
    ws4.row_dimensions[r_idx].height = 38
    fill_row = fill_zebra if r_idx % 2 == 0 else fill_white
    row_values = [
        item["code"], item["name"], item["condition"], item["patterns"],
        item["platforms"], item["internal_action"], item["ui_presentation"], item["user_guidance"]
    ]
    for c_idx, val in enumerate(row_values, start=1):
        c = ws4.cell(row=r_idx, column=c_idx, value=val)
        c.fill = fill_row
        c.border = border_all_thin
        c.alignment = alignments_err.get(c_idx, align_left_top)
        if c_idx == 1:
            c.font = font_code
        elif c_idx == 2:
            c.font = font_bold
        else:
            c.font = font_regular

last_col_err = get_column_letter(len(headers_err))
ws4.auto_filter.ref = f"A1:{last_col_err}{len(SITE_ISSUE_TREE) + 1}"

ws4.column_dimensions["A"].width = 24
ws4.column_dimensions["B"].width = 28
ws4.column_dimensions["C"].width = 36
ws4.column_dimensions["D"].width = 38
ws4.column_dimensions["E"].width = 26
ws4.column_dimensions["F"].width = 42
ws4.column_dimensions["G"].width = 34
ws4.column_dimensions["H"].width = 46


# =============================================================================
# SHEET 5: RESTful API 契约字典 (API Endpoints & Error Branches)
# =============================================================================
ws5 = wb.create_sheet(title="RESTful API 契约字典")
ws5.views.sheetView[0].showGridLines = True
ws5.freeze_panes = "A2"
ws5.row_dimensions[1].height = 28

headers_a = [
    "接口编号", "HTTP 方法", "请求路径 (Path)", "接口功能说明",
    "请求参数 / 请求体 Schema", "响应格式 (Response Schema)",
    "安全防护与访问约束", "常见 HTTP 异常状态码"
]

for col_idx, h in enumerate(headers_a, start=1):
    c = ws5.cell(row=1, column=col_idx, value=h)
    c.font = font_table_header
    c.fill = fill_header_navy
    c.alignment = align_center
    c.border = border_all_thin

alignments_a = {
    1: align_center_top, 2: align_center_top, 3: align_left_top,
    4: align_left_top, 5: align_left_top, 6: align_left_top,
    7: align_left_top, 8: align_left_top
}

for r_idx, item in enumerate(API_ENDPOINTS, start=2):
    ws5.row_dimensions[r_idx].height = 26
    fill_row = fill_zebra if r_idx % 2 == 0 else fill_white
    row_values = [
        item["id"], item["method"], item["path"], item["name"],
        item.get("params", ""), item.get("response", ""),
        item.get("security", ""), item.get("errors", "")
    ]
    for c_idx, val in enumerate(row_values, start=1):
        c = ws5.cell(row=r_idx, column=c_idx, value=val)
        c.fill = fill_row
        c.border = border_all_thin
        c.alignment = alignments_a.get(c_idx, align_left_top)

        if c_idx == 1:
            c.font = font_bold
        elif c_idx == 2:
            m = str(val)
            c.font = font_bold
            if m == "GET":
                c.font = Font(name=font_family, size=9, bold=True, color="137333")
            elif m == "POST":
                c.font = Font(name=font_family, size=9, bold=True, color="1A73E8")
            elif m == "PUT":
                c.font = Font(name=font_family, size=9, bold=True, color="B06000")
            elif m == "DELETE":
                c.font = Font(name=font_family, size=9, bold=True, color="C5221F")
            elif m == "Middleware":
                c.font = Font(name=font_family, size=9, bold=True, color="6A1B9A")
        elif c_idx == 3:
            c.font = font_code
        else:
            c.font = font_regular

last_col_a = get_column_letter(len(headers_a))
ws5.auto_filter.ref = f"A1:{last_col_a}{len(API_ENDPOINTS) + 1}"

ws5.column_dimensions["A"].width = 12
ws5.column_dimensions["B"].width = 14
ws5.column_dimensions["C"].width = 26
ws5.column_dimensions["D"].width = 26
ws5.column_dimensions["E"].width = 40
ws5.column_dimensions["F"].width = 38
ws5.column_dimensions["G"].width = 36
ws5.column_dimensions["H"].width = 30


# =============================================================================
# SHEET 6: 前端交互全场景状态机 (UI State Machine & Interactions)
# =============================================================================
ws6 = wb.create_sheet(title="前端交互全场景状态机")
ws6.views.sheetView[0].showGridLines = True
ws6.freeze_panes = "A2"
ws6.row_dimensions[1].height = 28

headers_ui = [
    "前端核心组件", "典型业务交互场景", "触发前置条件与用户输入",
    "系统响应与前端执行动作", "界面视觉呈现与动画反馈", "极端边界与异常容灾表现"
]

for col_idx, h in enumerate(headers_ui, start=1):
    c = ws6.cell(row=1, column=col_idx, value=h)
    c.font = font_table_header
    c.fill = fill_header_navy
    c.alignment = align_center
    c.border = border_all_thin

alignments_ui = {
    1: align_center_top, 2: align_left_top, 3: align_left_top,
    4: align_left_top, 5: align_left_top, 6: align_left_top
}

for r_idx, item in enumerate(UI_STATE_MACHINE, start=2):
    ws6.row_dimensions[r_idx].height = 36
    fill_row = fill_zebra if r_idx % 2 == 0 else fill_white
    row_values = [
        item["component"], item["scene"], item["condition"],
        item["action"], item["ui_response"], item["edge_case"]
    ]
    for c_idx, val in enumerate(row_values, start=1):
        c = ws6.cell(row=r_idx, column=c_idx, value=val)
        c.fill = fill_row
        c.border = border_all_thin
        c.alignment = alignments_ui.get(c_idx, align_left_top)
        c.font = font_bold if c_idx == 1 else font_regular

last_col_ui = get_column_letter(len(headers_ui))
ws6.auto_filter.ref = f"A1:{last_col_ui}{len(UI_STATE_MACHINE) + 1}"

ws6.column_dimensions["A"].width = 28
ws6.column_dimensions["B"].width = 28
ws6.column_dimensions["C"].width = 36
ws6.column_dimensions["D"].width = 38
ws6.column_dimensions["E"].width = 42
ws6.column_dimensions["F"].width = 44


# =============================================================================
# SHEET 7: 操作系统集成与底层规范字典 (OS Integration & Low-level Specs)
# =============================================================================
ws7 = wb.create_sheet(title="操作系统集成与底层规范字典")
ws7.views.sheetView[0].showGridLines = True
ws7.freeze_panes = "A2"
ws7.row_dimensions[1].height = 28

headers_os = [
    "规范项编号", "底层技术领域 / 操作系统", "规范 / 常量 / 接口名称",
    "配置数值 / 技术实现原理", "生效阶段与触发条件", "安全收益与防崩溃防泄漏作用"
]

for col_idx, h in enumerate(headers_os, start=1):
    c = ws7.cell(row=1, column=col_idx, value=h)
    c.font = font_table_header
    c.fill = fill_header_navy
    c.alignment = align_center
    c.border = border_all_thin

os_specs_data = [
    ("SPEC-01", "Windows 内核作业对象 (Win32)", "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (0x2000)", "LimitFlags |= 0x2000；将主服务与派生子进程纳入同一个 Job Object", "主进程启动时初始化", "主进程崩溃、退出或被任务管理器结束时，Windows 内核强制同步瞬间清除全部 Playwright、FFmpeg 子进程，零孤儿进程残留"),
    ("SPEC-02", "Windows 句柄安全继承 (Win32)", "PROC_THREAD_ATTRIBUTE_HANDLE_LIST", "STARTUPINFOEX.lpAttributeList 白名单继承，仅传递 lock_fd 句柄", "启动后端 Python 子进程时", "严防其他网络套接字或敏感文件句柄被子进程意外继承导致句柄泄露"),
    ("SPEC-03", "Windows 密码学接口 (DPAPI)", "CryptProtectData / CryptUnprotectData", "crypt32.dll 调用，dwFlags=1 (CRYPTPROTECT_UI_FORBIDDEN)，绑定当前 Windows 用户主密钥", "写入或读取 data/yuanbao-cookie.txt", "硬件与用户会话级别加密，严禁明文存储凭证；非当前 Windows 用户无法解密，彻底杜绝凭证被拷走泄露"),
    ("SPEC-04", "Windows 资源管理器 COM 自动化", "Shell.Application STA 反射调度", "通过 Python.NET (clr) 在 STA 线程反射调用 Shell.Application，枚举 LocationURL 匹配文件目录", "用户点击【打开文件夹】或【定位】", "调用 user32.SetForegroundWindow 精准将已打开的 Explorer 置顶前台，若最小化调用 ShowWindow(handle, 9) 恢复"),
    ("SPEC-05", "流密码对称解密算法", "ISAAC64 流式解密", "黄金分割比 0x9E3779B97F4A7C13 初始化 256 项 64 位数组，4 轮 _mix 迭代打散，前 128KB 逐字节 XOR", "微信视频号媒体流下载时", "还原微信视频加密头部，内存开销恒定，解密耗时 <20ms，后半段明文直接拼接"),
    ("SPEC-06", "媒体安全打开白名单", "OPENABLE_MEDIA_SUFFIXES (23 种)", "frozenset({.mp4, .mkv, .webm, .mov, .m4v, .avi, .flv, .ts, .mp3, .m4a, .aac, .flac, .ogg, .wav, .opus, .jpg, .jpeg, .png, .webp, .gif, .avif, .heic, .heif, .bmp})", "用户点击卡片【播放】或系统打开", "同时校验原始后缀与符号链接解析后真实后缀，严禁直接执行 .exe, .bat, .vbs, .lnk 等可执行文件，防恶意投毒执行"),
    ("SPEC-07", "字符串截断与组件上限", "MAX_TITLE_BYTES / MAX_MEDIA_ID_BYTES", "MAX_TITLE_BYTES=180 字节，MAX_MEDIA_ID_BYTES=64 字节，MAX_FILENAME_COMPONENT_BYTES=255 字节", "下载前生成本地文件名时", "严格按 UTF-8 字符边界截断，防止产生非法半字符乱码，保证文件名符合 Windows MAX_PATH 与 NTFS 255 字节组件规范"),
    ("SPEC-08", "分段探测与流式分块", "DOUYIN_PROBE_BYTES / STREAM_CHUNK_BYTES", "DOUYIN_PROBE_BYTES = 256KB，DOUYIN_STREAM_CHUNK_BYTES = 64KB", "媒体质量探测与文件流传输时", "探测阶段仅消耗 256KB 极少流量完成 FFprobe 实测；传输阶段以 64KB 持续写入磁盘，内存占用恒定，零爆内存"),
    ("SPEC-09", "双轨闲置超时守护", "DOUYIN_QUALITY_PROBE_IDLE_TIMEOUT_SECONDS", "闲置超时设为 120.0 秒 (QUALITY_PROBE_IDLE & TRANSFER_IDLE)；单次 HTTP 超时 30.0 秒", "网络假死挂起无数据流出时", "连续 120 秒未接收到任何有效字节立即强制断开连接并抛出超时异常，释放任务线程与网络连接池"),
    ("SPEC-10", "重定向安全与跳数上限", "DOUYIN_MAX_MEDIA_REDIRECTS / REGIONAL_DOMAINS", "最大重定向次数限制为 5 次；仅放行 douyin.com, douyinvod.com, amemv.com, zjcdn.com 等白名单域名", "媒体流经历 302 跨 CDN 线路跳转时", "严格逐跳校验 Host 白名单与标准 443 端口，超过 5 跳抛出异常，彻底杜绝恶意重定向死循环与 SSRF 攻击"),
    ("SPEC-11", "单作品时长安全红线", "DOUYIN_MAX_DURATION_MS (7 天)", "duration_ms ≤ 7 * 24 * 60 * 60 * 1000 (604,800,000 毫秒)", "解析作品元数据时", "拦截无限时长直播流或异常脏数据，避免生成无限写入的大文件挤爆用户物理磁盘"),
    ("SPEC-12", "移动端专属 UA 注入", "DOUYIN_MEDIA_HEADERS", "Linux; Android 14; Pixel 8 Pro Chrome/131.0 Mobile Safari/537.36", "向官方接口请求媒体流与详情时", "获得移动端专享的高清无水印视频分发流，显著降低平台 Web 端风控拦截概率"),
    ("SPEC-13", "FFprobe 探测安全边界", "FFPROBE_PIPE_TIMEOUT / FILE_TIMEOUT", "管道探测超时 3.0 秒，文件探测超时 15.0 秒，最大探测文件体积 8GB (DOUYIN_MAX_PROBE_FILE_BYTES)", "调用 FFprobe 实测媒体规格时", "超时未退出直接调用 kill() 杀死子进程，抛出 MediaDownloadError，防止坏道文件造成后台进程假死"),
    ("SPEC-14", "POSIX 进程守卫与管道监听", "run_guarded_command (Linux/macOS)", "通过 select.select 监听父进程管道 FD；父进程退出时管道关闭，守卫进程自动向进程组发送 SIGTERM 与 SIGKILL", "POSIX 系统中启动子命令时", "跨平台保障进程树生命周期一致性，父进程死亡子进程随之同归于尽"),
    ("SPEC-15", "停机控制与响应体保护", "stop.py 16KB 截断与防误杀", "_MAX_RESPONSE_BYTES = 16KB (16384 bytes)；校验 build_id 与项目根目录一致性", "执行停机脚本 stop.py 时", "防止目标端口被恶意程序占用导致内存溢出；仅停机已确权属于本项目的后端进程")
]

for r_idx, item in enumerate(os_specs_data, start=2):
    ws7.row_dimensions[r_idx].height = 36
    fill_row = fill_zebra if r_idx % 2 == 0 else fill_white
    for c_idx, val in enumerate(item, start=1):
        c = ws7.cell(row=r_idx, column=c_idx, value=val)
        c.fill = fill_row
        c.border = border_all_thin
        if c_idx in (1, 2):
            c.font = font_bold
            c.alignment = align_center_top
        elif c_idx == 3:
            c.font = font_code
            c.alignment = align_left_top
        else:
            c.font = font_regular
            c.alignment = align_left_top

last_col_os = get_column_letter(len(headers_os))
ws7.auto_filter.ref = f"A1:{last_col_os}{len(os_specs_data) + 1}"

ws7.column_dimensions["A"].width = 14
ws7.column_dimensions["B"].width = 28
ws7.column_dimensions["C"].width = 38
ws7.column_dimensions["D"].width = 44
ws7.column_dimensions["E"].width = 26
ws7.column_dimensions["F"].width = 46

wb.save(OUTPUT_FILE)
print(f"Grand Ultimate Workbook '{OUTPUT_FILE}' with 7 sheets generated successfully!")
