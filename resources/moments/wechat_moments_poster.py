# -*- coding: utf-8 -*-
"""
微信朋友圈自动发布脚本（Windows / 微信 4.x PC 客户端）
=========================================================

技术路线（严格限定）：
    屏幕截图（mss） + DIP 常量定位（客户区/窗口锚点 + 实测 DPI 缩放）
    + 模拟键鼠（pyautogui） + 窗口管理（pywin32）

    运行时**不依赖** templates/ 目录：所有可点击元素都由 config.json 的
    `anchor.items` 常量（DIP，锚定客户区/窗口边缘）按实测 DPI 缩放换算成物理像素。
    模板只保留给 `--debug-templates`（打印常量预测点与模板实测中心的偏差），
    模板缺失不影响任何流程步骤。

    每一步点击后都做**效果验证**（区域像素变化 / 区域内容特征 / 窗口标题），
    验证不通过立即抛错退出，绝不在旧坐标上继续往下点。

明确不使用：微信 API、协议逆向、进程注入/Hook、uiautomation 控件树。

本机实测流程（微信 4.2.0.13 / 1920x1080 / 150% 缩放）：
    1. 按标题“微信”找到主窗口并前置
    2. 点击左侧罗盘图标 -> 展开“发现”浮层（点击后验证浮层位置出现浅色面板行）
    3. 点击浮层中的“朋友圈” -> 朋友圈页面打开（验证相机位置附近有内容 + 客户区重绘）
       （本机 4.2.0.13 实测：页面直接显示在主窗口内，不一定新开标题为“朋友圈”的独立窗口）
    4. 点击朋友圈右上角相机图标 -> 弹出“选择文件”对话框（按窗口标题验证）
       （该版本必须先选至少一张图片，才会出现编辑面板；纯文字发表入口未找到）
    5. 在对话框文件名框粘贴图片路径并回车 -> 编辑面板出现（验证可见范围行有内容）
    6. 点击“这一刻的想法...”输入文字；点“+”方块可继续添加图片
    7. 点击绿色“发表”按钮
    8. 校验编辑面板关闭（相对面板指纹的像素变化）+ 信息流首屏像素差

说明：
    - 不同微信版本入口可能不同。若某版本点相机后直接出现编辑面板，
      脚本会同时轮询“编辑面板”和“文件对话框”两种信号，自动适配，无需改配置。
    - 若本机只配置文字、未配置图片，而该版本要求先选图，脚本会以
      错误码 2 明确报错，而不是盲点。

安全机制：
    - 启动倒计时（可配置秒数）
    - pyautogui FAILSAFE：鼠标移到屏幕角落立即异常中止
    - dry-run：只打印锚点换算结果，不点击不输入
    - --no-publish：走完真实流程但停在“发表”按钮前，不真正发布（安全彩排）
    - 点击护栏：由常量算出的「发表」按钮矩形，除发表步骤外任何落进去的点击
      一律拒绝（退出码 10）
    - 窗口尺寸中途变化：先等重绘再复核；仍在变化则报错退出，不在旧坐标上点
    - 任一步骤效果验证失败：日志 + 失败截图 + 明确错误码退出，绝不盲点

退出码：
    0 成功 / 2 配置错误 / 3 模板读取失败（仅 --debug-templates 路径）
    4 找不到微信窗口 / 5 定位或效果验证失败
    6 文件对话框失败 / 7 发表校验失败 / 8 整体超时 / 9 用户中止
    10 点击护栏拦截（目标落在「发表」按钮内） / 99 未预期异常

用法示例：
    python wechat_moments_poster.py --dry-run
    python wechat_moments_poster.py --text "今天天气不错" --image D:\\pics\\a.jpg
    python wechat_moments_poster.py --config config.json --no-publish
    python wechat_moments_poster.py --dry-run --debug-templates   # 打印常量与模板偏差
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import re
import shutil
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


# ============================================================================
# 一、DPI 感知：必须在导入 pyautogui / mss 之前执行
# ============================================================================
def enable_dpi_awareness() -> None:
    """
    声明 DPI 感知，让“截图像素坐标”和“鼠标物理坐标”处于同一坐标系。

    本机实测：物理 1920x1080，系统缩放 150%（逻辑 1280x720）。
    若不声明，mss 只能截到左上角 1280x720 的模糊图，点击坐标也会错位。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor DPI Aware
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


enable_dpi_awareness()

try:
    # 控制台中文输出统一 UTF-8，避免 GBK 编码异常
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    # 仅模板匹配 / --debug-templates 使用；运行时定位全部走 DIP 常量，缺失不影响主流程
    import cv2  # noqa: E402
except Exception:  # pragma: no cover
    cv2 = None
import mss  # noqa: E402
import numpy as np  # noqa: E402
import pyautogui  # noqa: E402
import pyperclip  # noqa: E402
import win32api  # noqa: E402
import win32con  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402

pyautogui.FAILSAFE = True   # 鼠标移到屏幕角落立即中止
pyautogui.PAUSE = 0.15


# ============================================================================
# 像素工具（纯 numpy / Pillow 实现，不依赖 OpenCV —— 便于打包成小体积运行时）
# ============================================================================
def to_gray(image: np.ndarray) -> np.ndarray:
    """BGR / BGRA -> 灰度，权重与 cv2.COLOR_BGR2GRAY 一致（0.114B + 0.587G + 0.299R）。"""
    if image is None:
        return None
    if image.ndim == 2:
        return image.astype(np.uint8)
    weights = np.array([0.114, 0.587, 0.299], dtype=np.float32)
    return np.dot(image[..., :3].astype(np.float32), weights).astype(np.uint8)


def gray_abs_diff_ratio(a: np.ndarray, b: np.ndarray, tolerance: int) -> float:
    """灰度差 > tolerance 的像素占比（用于“点击后真的变了”的判据）。"""
    ga, gb = to_gray(a), to_gray(b)
    diff = np.abs(ga.astype(np.int16) - gb.astype(np.int16))
    return float(np.count_nonzero(diff > int(tolerance))) / float(diff.size)


def laplacian_var(gray: np.ndarray) -> float:
    """4 邻域拉普拉斯方差（等价 cv2.Laplacian(gray, CV_32F) 的 .var()）。"""
    if gray is None or gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    g = gray.astype(np.float32)
    lap = (g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
           - 4.0 * g[1:-1, 1:-1])
    return float(lap.var())


def save_png(path: str, bgr: np.ndarray) -> None:
    """保存 BGR(A) 截图为 PNG（Pillow 编码，不依赖 OpenCV）。"""
    from PIL import Image
    rgb = bgr[:, :, ::-1] if bgr.ndim == 3 else bgr
    Image.fromarray(np.ascontiguousarray(rgb)).save(path)


if getattr(sys, "frozen", False):
    # PyInstaller 冻结版：__file__ 在临时解包目录里，配置/日志必须相对 exe 所在目录
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================================
# 二、退出码
# ============================================================================
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_TEMPLATE_MISSING = 3
EXIT_WINDOW_NOT_FOUND = 4
EXIT_LOCATE_FAILED = 5
EXIT_DIALOG_FAILED = 6
EXIT_PUBLISH_VERIFY_FAILED = 7
EXIT_TIMEOUT = 8
EXIT_USER_ABORT = 9
EXIT_SAFETY_BLOCKED = 10          # 安全护栏拦截：点击目标落在“发表”按钮内
EXIT_UNEXPECTED = 99


class StepError(Exception):
    """步骤失败：携带退出码和步骤名。"""

    def __init__(self, code: int, step: str, message: str):
        super().__init__(f"[{step}] {message}")
        self.code = code
        self.step = step
        self.message = message


class DryRunStop(Exception):
    """dry-run 模式下遇到必须真实操作才能继续的步骤，正常结束。"""


class UserAbort(Exception):
    """用户中止（倒计时移鼠标到角落 / FailSafe）。"""


# ============================================================================
# 三、默认配置
# ============================================================================
DEFAULT_CONFIG: Dict = {
    "windows": {
        "main": {"title_regex": r"^(微信|WeChat)$"},
        "moments": {"title_regex": r"朋友圈"},
        "file_dialog": {"title_regex": r"(打开|Open|选择文件)"},
    },
    "templates": {
        "dir": "templates",
        "threshold": 0.80,
        # 多尺度：模板按这些比例缩放后匹配，适配不同分辨率/DPI
        "scales": [1.0, 0.9, 0.8, 1.1, 1.25, 1.5],
        "items": {
            "sidebar_moments": {
                "file": "wc_sidebar_moments.png",
                "alt_files": ["wc_sidebar_moments_active.png"],
                "roi": [0.0, 0.0, 0.10, 1.0],   # 相对微信主窗口
                "threshold": 0.85,
                "desc": "主窗口左侧导航栏的“朋友圈”图标（灰色罗盘/激活绿色对勾）",
            },
            "discover_moments": {
                "file": "wc_discover_moments.png",
                "alt_files": ["wc_discover_moments_active.png"],
                "roi": [0.0, 0.0, 0.45, 1.0],   # 相对微信主窗口
                "threshold": 0.82,
                "desc": "“发现”浮层里的“朋友圈”一行（灰色/绿色选中两种外观）",
            },
            "moments_camera": {
                "file": "wc_moments_camera.png",
                # 朋友圈窗口坐标在不同版本/布局下不稳定，这里全屏搜索
                "roi": None,
                "threshold": 0.85,
                "desc": "朋友圈页面右上角的相机（发表）图标",
            },
            "editor_input": {
                "file": "wc_editor_input.png",
                "roi": None,
                "threshold": 0.75,
                "desc": "编辑面板里的“这一刻的想法...”占位文字",
            },
            "editor_add_image": {
                "file": "wc_editor_add_image.png",
                "roi": None,
                "threshold": 0.82,
                "desc": "编辑面板里继续添加图片的“+”方块",
            },
            "editor_visibility": {
                "file": "wc_editor_visibility.png",
                "roi": None,
                "threshold": 0.85,
                "desc": "编辑面板里的“谁可以看 … 公开”整行（只用于判断编辑面板已出现）",
            },
            "editor_publish": {
                "file": "wc_editor_publish.png",
                "roi": None,
                "threshold": 0.85,
                "desc": "编辑面板里的绿色“发表”按钮",
            },
            "editor_cancel": {
                "file": "wc_editor_cancel.png",
                "roi": None,
                "threshold": 0.85,
                "desc": "编辑面板里的灰色「取消」按钮（清理草稿用）",
            },
            "discard_no_keep": {
                "file": "wc_discard_nokeep.png",
                "roi": None,
                "threshold": 0.85,
                "optional": True,
                "desc": "“将此次编辑保留？”弹窗里的「不保留」按钮",
            },
            "publish_confirm": {
                "file": "wc_publish_confirm.png",
                "optional": True,
                "roi": None,
                "threshold": 0.85,
                "desc": "（可选）发表后的二次确认按钮",
            },
        },
    },
    # ========================================================================
    # DIP 常量锚点（唯一的位置真值来源）
    #
    # 测量环境（这 5 个 DIP 常量只在这个环境下实测过）：
    #   分辨率 1920x1080 / 系统缩放 150% / 微信 PC 4.2.0.13 / 浅色主题
    #   参照系：客户区（GetClientRect + ClientToScreen）与窗口外框（GetWindowRect）
    #
    # dip = 物理像素偏移 / 1.5；运行时用“dip × 实测 DPI 缩放”换算，不写死物理像素。
    # frame 取值见 anchor.frames：
    #   client_tl          客户区左上角为原点
    #   client_center      客户区几何中心为原点
    #   window_right_top   窗口外框右缘为 x0、上缘为 y0（负数向左）
    # ========================================================================
    "anchor": {
        "measured_env": {
            "resolution": "1920x1080",
            "dpi_scale": 1.5,
            "wechat_version": "4.2.0.13",
            "theme": "light",
            "frame": "client-area",
            "note": "dip 值来自 .dip-probe/result_v4/v6/v7 的模板命中中心实测；"
                    "客户区与窗口外框的差：左 11 / 右 11 / 上 0 / 下 11（物理像素 @150%）",
        },
        "frames": {
            "client_tl": "客户区左上角",
            "client_center": "客户区几何中心",
            "window_right_top": "窗口外框右缘 / 上缘",
        },
        # 仅调试：templates 存在时打印“常量预测点 vs 模板实测中心”偏差，不参与定位
        "debug_templates": False,
        "items": {
            "sidebar_moments": {
                "frame": "client_tl", "dip": [28.67, 254.67], "size": [28.0, 28.0],
                "desc": "左侧导航栏「朋友圈」图标（灰色罗盘 / 激活绿色对勾）",
            },
            "discover_moments": {
                "frame": "client_tl", "dip": [110.67, 100.67], "size": [80.0, 30.0],
                "desc": "“发现”浮层里的「朋友圈」一行",
            },
            "moments_camera": {
                "frame": "window_right_top", "dip": [-85.2, 55.33], "size": [25.3, 18.7],
                "desc": "朋友圈页面右上角相机（发表）图标",
            },
            # ------------------------------------------------------------------
            # 编辑面板有**两种高度布局**（二值跳变，不是连续变化）：
            #   single_row  1~2 张图（图片 1 行）      -> dip 为下面的默认值
            #   multi_row   3~9 张图（图片 2~3 行）    -> variants.multi_row 覆盖
            # 实测（2026-11-07 / 客户区中心 (750,492) / 客户区 1478x950 / 150%）：
            #   1-2 张：input y=199 publish y=782 cancel y=780（可见范围行 y=695 可见）
            #   3 张 / 6 张 / 9 张：input y=137、publish y=843、cancel y=841，
            #       三者完全一致（面板跳到固定的加高布局），且“谁可以看/提醒谁看”
            #       与“+”位置都会变化；“谁可以看 … 公开”整行文本消失（模板匹配不到）。
            # ------------------------------------------------------------------
            "editor_visibility": {
                "frame": "client_center", "dip": [3.6, 135.5], "size": [306.7, 33.3],
                "desc": "编辑面板“谁可以看 … 公开”整行（单行布局的面板就绪判据）",
            },
            "editor_input": {
                "frame": "client_center", "dip": [-71.7, -195.2], "size": [166.7, 28.0],
                "variants": {"multi_row": {"dip": [-72.0, -236.7]}},
                "desc": "编辑面板“这一刻的想法...”输入区",
            },
            "editor_add_image": {
                "frame": "client_center", "dip": [-9.1, -41.2], "size": [86.7, 88.0],
                "desc": "编辑面板“+”添加图片方块（9 张时该方块消失）",
            },
            "editor_publish": {
                "frame": "client_center", "dip": [-61.1, 193.5], "size": [110.0, 25.3],
                "variants": {"multi_row": {"dip": [-61.3, 234.0]}},
                "desc": "编辑面板绿色「发表」按钮（同时用于点击护栏与多行布局就绪判据）",
            },
            "editor_cancel": {
                "frame": "client_center", "dip": [56.3, 192.1], "size": [121.3, 33.3],
                "variants": {"multi_row": {"dip": [56.0, 232.7]}},
                "desc": "编辑面板灰色「取消」按钮",
            },
            "editor_grid": {
                # 3×3 图片网格：第 1 格中心 + 步进 + 格子尺寸（均为 DIP，相对客户区中心）
                "frame": "client_center", "dip": [-101.33, -89.33],
                "pitch": [100.0, 100.0], "cell": [95.33, 95.33],
                "desc": "编辑面板图片网格（实测格中心 x=598/748/898、y=358/508/658，格子 143px）",
            },
            "discard_no_keep": {
                "frame": "client_center", "dip": [56.3, 24.8], "size": [108.0, 34.0],
                "desc": "“将此次编辑保留？”弹窗里的「不保留」按钮",
            },
            "file_dialog": {
                # 实测（2026-11-07 彩排）：对话框外框左上角 = 客户区左上角（偏差 0,0），
                # 尺寸固定 960x720 物理像素。dip 相对客户区左上角，故为 0。
                "frame": "client_tl", "dip": [0.0, 0.0], "size": [640.0, 480.0],
                "origin": "top_left",
                "desc": "“选择文件”对话框的位置（左上角，相对客户区）与固定尺寸",
            },
        },
        # 编辑面板布局切换与图片上限
        "editor_layout": {
            # 实测：图片数 >= 3 时面板跳到“加高布局”（2~3 行图片），<=2 张仍是单行布局
            "multi_row_min_images": 3,
            "max_images": 9,        # 微信朋友圈单条最多 9 张图（3 行 × 3 列）
            "grid_cols": 3,
        },
        # 多图一次性导入：把文件对话框切到 staging 文件夹 -> 全选 -> 回车
        "multi_image": {
            # 文件对话框里第一个文件缩略图中心（DIP，相对对话框左上角）
            "first_tile_dip": [242.67, 163.33],
            "enter_folder_settle_sec": 1.3,
            "max_selected": 9,
        },
        # 效果验证判据（像素统计口径：mean 灰度均值 / std 灰度标准差 /
        # dark 灰度<128 的占比 / edge Laplacian 方差 / green 绿色像素占比）
        "verify": {
            "pixel_tolerance": 25,        # 单像素灰度差超过该值才算“变化”
            "poll_interval_sec": 0.35,
            "resize_settle_sec": 1.5,     # 检测到窗口尺寸变化后等待重绘的时间
            "discover": {                 # 点侧边栏 -> 浮层出现
                "min_diff": 0.15,         # 浮层区域像素变化比例下限
                "min_mean": 200.0,        # 浮层是浅色面板（未选中态）
                "max_dark": 0.20,
                "min_edge": 600.0,        # 行内必须有文字/分隔线
                # 选中态：点侧边栏「朋友圈」图标后，浮层里的「朋友圈」行会变成绿色高亮，
                # 此时该行 mean 只有 ~138（绿色灰度≈126），必须用“绿色占比”来认，
                # 但绿色行在聊天页（第一条会话被选中）也会出现，所以只在“点击后 + 区域确实变化”
                # 的场景下作为有效信号（allow_selected=True）。
                "min_green_row": 0.30,
                "timeout_sec": 5.0,
            },
            "moments": {                  # 点浮层行 -> 朋友圈页就绪
                "min_page_diff": 0.05,    # 整块客户区发生变化
                "min_std": 8.0,           # 相机位置附近必须有内容（排除纯白加载占位）
                "min_dark": 0.01,
                "settle_timeout_sec": 6.0,  # 点侧边栏后先等页面内容出现，再点浮层行
                "timeout_sec": 20.0,
            },
            "editor": {                   # 选图回车 -> 编辑面板出现
                "min_diff": 0.10,         # 相对“回车前”判据区域的变化
                "min_mean": 215.0,        # 面板是浅色底（信息流里同位置通常更暗）
                "min_std": 8.0,
                "min_dark": 0.002,
                "max_dark": 0.18,
                # 多行布局没有“谁可以看 … 公开”文本行，改用「发表」按钮的绿色占比判据
                # （实测绿色按钮 green≈0.96，信息流里同位置 green≈0.00）
                "multi_min_green": 0.50,
                "timeout_sec": 20.0,
            },
            "grid": {                     # 图片网格完整性（多图导入后逐格采样）
                "min_content_std": 12.0,     # 有图片的格子：灰度标准差下限
                "max_content_mean": 228.0,   # 或整体偏暗（有内容）
                "empty_min_mean": 232.0,     # 空白/“+”格子：浅色底
                "empty_max_std": 11.0,       # 且几乎没有纹理
            },
            "panel_closed": {             # 面板关闭（取消/发表后）
                "min_diff": 0.12,         # 相对打开时留存的面板指纹
                "max_feed_diff": 0.30,    # 清理草稿时：该区域要重新像“发表前信息流首屏”
                "timeout_sec": 20.0,
            },
            "discard_dialog": {           # 点取消后是否弹出保留确认框
                # 实测：确认框里“不保留”按钮区域 mean=227 dark=0.038，
                # 与点之前（面板白色留白）的灰度差只有 0.06 的像素比例，
                # 所以这里用“有变化 + 出现了文字”，而不是只用一个较高的 diff 阈值
                "min_diff": 0.03,
                "min_dark": 0.02,
                "timeout_sec": 3.0,
            },
        },
    },
    "timing": {
        "countdown_sec": 5,
        "step_wait_sec": 0.8,
        "locate_timeout_sec": 8.0,
        "locate_retry_interval_sec": 0.4,
        "window_open_timeout_sec": 20.0,
        "moments_open_retries": 2,
        "dialog_timeout_sec": 15.0,
        "dialog_confirm_retries": 3,
        "total_timeout_sec": 240,
        "after_publish_wait_sec": 1.5,
        "editor_open_timeout_sec": 20.0,
    },
    "content": {
        "text": "",
        "text_file": "",
        "images": [],
    },
    "publish": {
        "enabled": True,
        "click_confirm_if_found": True,
        "cancel_after_no_publish": True,
        "verify_editor_closed_sec": 20.0,
        # 发表后校验：面板消失 + 信息流首屏像素差（+ 可选 OCR）
        "feed_verify": {
            "enabled": True,
            # 首屏区域，相对微信主窗口 (x0, y0, x1, y1)，0~1
            "roi": [0.30, 0.05, 1.0, 1.0],
            # 像素差比例阈值：变化像素占比 >= 该值才算“出现新内容”
            "diff_threshold": 0.02,
            # 单像素灰度差超过该值才算“变化”，过滤压缩噪声/轻微重绘
            "pixel_tolerance": 25,
            "scroll_to_top": True,
            "scroll_units": 30,
            "settle_sec": 1.2,
            # 信息流比对的最长等待（面板关闭后重新计时）
            "timeout_sec": 15.0,
            "ocr": {
                "enabled": True,
                "lang": "chi_sim",
                "min_chars": 6,
            },
        },
    },
    "safety": {
        "dry_run": False,
    },
    # 定时发表：at 与 in 二选一，留空则立即执行
    "schedule": {
        "at": "",                       # 绝对时间 "YYYY-MM-DD HH:MM:SS"
        "in": "",                       # 相对延迟 "20s" / "30m" / "2h"
        "progress_interval_sec": 60,    # 等待期间每隔多少秒打印一次剩余时间
        "min_lead_sec": 10,             # 距目标时间少于该秒数直接报错
    },
    "screen": {
        "expected_width": 1920,
        "expected_height": 1080,
        "expected_dpi_scale": 1.5,
    },
    "logging": {"dir": "logs"},
}


def deep_merge(base: Dict, override: Dict) -> Dict:
    """递归合并配置。"""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_path(path: str, base_dir: str) -> str:
    """相对路径基于脚本目录。"""
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(base_dir, path))


def load_config(path: Optional[str], base_dir: str) -> Dict:
    cfg = DEFAULT_CONFIG
    if path:
        cfg_path = resolve_path(path, base_dir)
        if not os.path.isfile(cfg_path):
            raise StepError(EXIT_CONFIG, "config", f"配置文件不存在：{cfg_path}")
        try:
            with open(cfg_path, "r", encoding="utf-8-sig") as f:
                user_cfg = json.load(f)
        except json.JSONDecodeError as exc:
            raise StepError(EXIT_CONFIG, "config", f"配置文件 JSON 解析失败：{exc}")
        cfg = deep_merge(DEFAULT_CONFIG, user_cfg)
    return cfg


def parse_schedule_time(at_str: str, in_str: str,
                        min_lead_sec: float = 10.0) -> Tuple[Optional[datetime], str]:
    """
    解析定时发表参数。

    at_str: "YYYY-MM-DD HH:MM:SS" 或 "YYYY-MM-DD HH:MM"（也接受用 T 分隔）
    in_str: "<数字><单位>"，单位 s/m/h，例如 20s、30m、2h

    返回 (目标时间, 描述)。没有配置定时则返回 (None, "")。
    时间格式错误、已过去、距现在不足 min_lead_sec，都抛 StepError(EXIT_CONFIG)，
    绝不静默按一个错误的时间去发表。
    """
    at_str = (at_str or "").strip()
    in_str = (in_str or "").strip()
    if at_str and in_str:
        raise StepError(EXIT_CONFIG, "schedule", "`--at` 与 `--in` 不能同时使用，请二选一")

    now = datetime.now()
    if at_str:
        text = at_str.replace("T", " ").strip()
        target = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                target = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if target is None:
            raise StepError(
                EXIT_CONFIG, "schedule",
                f"无法解析 `--at` 时间：{at_str!r}；正确格式为 \"YYYY-MM-DD HH:MM:SS\"（也接受 HH:MM）")
        desc = f"--at {at_str}"
    elif in_str:
        match = re.match(r"^(\d+(?:\.\d+)?)\s*(s|m|h)$", in_str, re.IGNORECASE)
        if not match:
            raise StepError(
                EXIT_CONFIG, "schedule",
                f"无法解析 `--in` 延迟：{in_str!r}；正确格式为 20s / 30m / 2h（单位必须是 s、m、h）")
        unit = {"s": 1, "m": 60, "h": 3600}[match.group(2).lower()]
        target = now + timedelta(seconds=float(match.group(1)) * unit)
        desc = f"--in {in_str}"
    else:
        return None, ""

    remain = (target - now).total_seconds()
    if remain < 0:
        raise StepError(EXIT_CONFIG, "schedule",
                        f"目标时间 {target:%Y-%m-%d %H:%M:%S} 已经过去（{ -remain:.0f} 秒前），请重新指定")
    if remain < float(min_lead_sec):
        raise StepError(EXIT_CONFIG, "schedule",
                        f"目标时间距现在只有 {remain:.1f} 秒，少于安全下限 {float(min_lead_sec):.0f} 秒；"
                        "请把时间往后调，或直接不指定定时参数立即执行")
    return target, desc


# ============================================================================
# 四、日志
# ============================================================================
class Logger:
    def __init__(self, log_dir: str):
        os.makedirs(log_dir, exist_ok=True)
        self.run_id = time.strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(log_dir, f"run_{self.run_id}.log")
        self.logger = logging.getLogger(f"moments.{self.run_id}.{id(self)}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()
        self.logger.propagate = False

        file_handler = logging.FileHandler(self.log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"))
        file_handler.setLevel(logging.DEBUG)

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        console.setLevel(logging.INFO)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console)

    def debug(self, msg, *args):
        self.logger.debug(msg, *args)

    def info(self, msg, *args):
        self.logger.info(msg, *args)

    def warning(self, msg, *args):
        self.logger.warning(msg, *args)

    def error(self, msg, *args):
        self.logger.error(msg, *args)


# ============================================================================
# 五、窗口工具
# ============================================================================
@dataclass
class WindowInfo:
    hwnd: int
    title: str
    rect: Tuple[int, int, int, int]


def list_visible_windows() -> List[WindowInfo]:
    windows: List[WindowInfo] = []

    def _cb(hwnd, _param):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return True
        try:
            if win32gui.IsIconic(hwnd):
                # 最小化窗口的 GetWindowRect 是 (-32000,-32000,...)，改用还原后的位置
                rect = tuple(win32gui.GetWindowPlacement(hwnd)[4])
            else:
                rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        left, top, right, bottom = rect
        if right - left < 50 or bottom - top < 50:
            return True
        windows.append(WindowInfo(hwnd, title, rect))
        return True

    win32gui.EnumWindows(_cb, None)
    return windows


def find_window(pattern: str, exclude_hwnd: Optional[int] = None) -> Optional[WindowInfo]:
    """按标题正则匹配，返回面积最大的窗口。"""
    regex = re.compile(pattern)
    candidates = [w for w in list_visible_windows()
                  if regex.search(w.title) and w.hwnd != exclude_hwnd]
    if not candidates:
        return None
    candidates.sort(key=lambda w: (w.rect[2] - w.rect[0]) * (w.rect[3] - w.rect[1]))
    return candidates[-1]


def activate_window(hwnd: int, retries: int = 6, interval: float = 0.25) -> bool:
    """
    前置窗口。SetForegroundWindow 受前台锁限制：
    先“临时置顶->取消置顶”+ BringWindowToTop，再模拟 Alt 键并 AttachThreadInput，
    最后轮询校验 GetForegroundWindow 是否真的是目标窗口。
    """
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass

    try:
        flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    except Exception:
        pass
    try:
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass

    for _ in range(retries):
        attached = False
        tid_cur = tid_fg = 0
        try:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            fg = win32gui.GetForegroundWindow()
            tid_fg = win32process.GetWindowThreadProcessId(fg)[0]
            tid_cur = win32api.GetCurrentThreadId()
            if tid_fg and tid_fg != tid_cur:
                attached = bool(ctypes.windll.user32.AttachThreadInput(tid_cur, tid_fg, True))
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
        except Exception:
            pass
        finally:
            if attached:
                try:
                    ctypes.windll.user32.AttachThreadInput(tid_cur, tid_fg, False)
                except Exception:
                    pass
        time.sleep(interval)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
    return win32gui.GetForegroundWindow() == hwnd


def get_dpi_scale(hwnd: int) -> float:
    """
    读取窗口所在显示器的 DPI 缩放（1.25 / 1.5 / 2.0 ...）。

    注意：进程已声明 Per-Monitor DPI Aware，因此这里的返回值就是
    “DIP 常量 -> 物理像素”的换算系数，不能写死成 1.5。
    """
    dpi = 0
    try:
        dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
    except Exception:
        dpi = 0
    if not dpi:
        try:
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
        except Exception:
            dpi = 0
    return (dpi or 96) / 96.0


def window_geometry(hwnd: int) -> Dict:
    """窗口外框 / 客户区的物理像素几何 + DPI 缩放。"""
    try:
        wl, wt, wr, wb = (int(v) for v in win32gui.GetWindowRect(hwnd))
        if win32gui.IsIconic(hwnd):
            wl, wt, wr, wb = (int(v) for v in win32gui.GetWindowPlacement(hwnd)[4])
    except Exception as exc:
        raise StepError(EXIT_WINDOW_NOT_FOUND, "geometry", f"读取窗口外框失败：{exc}")
    cx, cy = (int(v) for v in win32gui.ClientToScreen(hwnd, (0, 0)))
    cw, ch = (int(v) for v in win32gui.GetClientRect(hwnd)[2:])
    return {
        "window": (wl, wt, wr, wb),
        "client": (cx, cy, cw, ch),
        "scale": get_dpi_scale(hwnd),
    }


# ============================================================================
# 六、截图与模板匹配
# ============================================================================
@dataclass
class Match:
    name: str
    score: float
    left: int
    top: int
    width: int
    height: int
    scale: float

    @property
    def center(self) -> Tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2


def create_mss():
    """兼容 mss 新旧版本（新版用 mss.MSS()，旧版用 mss.mss()）。"""
    factory = getattr(mss, "MSS", None)
    if factory is not None:
        return factory()
    return mss.mss()


def feed_diff_ratio(baseline: np.ndarray, after: np.ndarray, tolerance: int = 25) -> float:
    """
    计算两张信息流首屏截图的像素差比例（0~1）。

    只统计“灰度差 > tolerance”的像素，过滤 PNG/渲染噪声；
    返回变化像素数 / 总像素数。尺寸不一致直接抛错——说明窗口被移动/缩放了，
    此时像素比对没有意义，必须让调用方明确失败，而不是给出一个错误的通过结论。
    """
    if baseline.shape[:2] != after.shape[:2]:
        raise StepError(EXIT_PUBLISH_VERIFY_FAILED, "verify",
                        f"信息流截图尺寸不一致：{baseline.shape[:2]} vs {after.shape[:2]}，"
                        "窗口可能在运行中被移动/缩放，无法做像素比对")
    return gray_abs_diff_ratio(baseline, after, int(tolerance))


def ocr_keyword_check(img: np.ndarray, text: str, ocr_cfg: Dict,
                      logger: Logger) -> Tuple[Optional[bool], str]:
    """
    可选 OCR：识别首屏截图，检查刚发文字的关键片段是否出现。

    返回值：
      True  -> 命中
      False -> 未命中（调用方应继续等待/最终判失败）
      None  -> 降级跳过（没装 pytesseract、没装 tesseract、缺 chi_sim 语言包、
               或 OCR 执行异常）。跳过只记 DEBUG，绝不能让整个校验挂掉。
    """
    if not ocr_cfg.get("enabled", True):
        logger.debug("OCR 未启用，跳过 OCR 判据")
        return None, "跳过（未启用）"
    try:
        import pytesseract  # 可选依赖，未安装时走降级分支
    except Exception:
        logger.debug("未安装 pytesseract，OCR 判据跳过（像素差判据仍然有效）")
        return None, "跳过（无 pytesseract）"
    lang = str(ocr_cfg.get("lang", "chi_sim"))
    try:
        langs = set(pytesseract.get_languages(config=""))
    except Exception as exc:
        logger.debug("无法读取 tesseract 语言列表（%s），OCR 判据跳过", exc)
        return None, "跳过（语言列表不可用）"
    if lang not in langs:
        logger.debug("tesseract 缺少语言包 %s（可用：%s），OCR 判据跳过",
                     lang, ", ".join(sorted(langs)))
        return None, f"跳过（缺少语言包 {lang}）"

    needle = re.sub(r"\s+", "", text)[:int(ocr_cfg.get("min_chars", 6))]
    if not needle:
        logger.debug("文字为空，OCR 判据跳过")
        return None, "跳过（文字为空）"
    try:
        gray = to_gray(img)
        gray = np.repeat(np.repeat(gray, 2, axis=0), 2, axis=1)  # 2x 放大，提升小字号识别率
        data = pytesseract.image_to_string(gray, lang=lang)
    except Exception as exc:
        logger.debug("OCR 执行失败（%s），判据跳过", exc)
        return None, "跳过（OCR 执行失败）"
    clean = re.sub(r"\s+", "", data)
    hit = needle in clean
    logger.info("OCR 校验：%s（关键片段「%s」）", "命中" if hit else "未命中", needle)
    return hit, ("命中" if hit else "未命中")


class ScreenGrabber:
    """mss 整屏/区域抓取，返回 BGR。"""

    def __init__(self):
        self.sct = create_mss()
        self.monitor = self.sct.monitors[1]
        self.screen_rect = (self.monitor["left"], self.monitor["top"],
                            self.monitor["left"] + self.monitor["width"],
                            self.monitor["top"] + self.monitor["height"])

    @property
    def width(self) -> int:
        return self.monitor["width"]

    @property
    def height(self) -> int:
        return self.monitor["height"]

    def grab(self) -> np.ndarray:
        raw = np.array(self.sct.grab(self.monitor))
        return np.ascontiguousarray(raw[:, :, :3])   # BGRA -> BGR（丢弃 alpha）

    def grab_rect(self, rect: Tuple[int, int, int, int]) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
        """抓取指定矩形，返回 (图像, 实际区域)。"""
        left, top, right, bottom = rect
        sl, st, sr, sb = self.screen_rect
        left, top = max(left, sl), max(top, st)
        right, bottom = min(right, sr), min(bottom, sb)
        if right - left < 4 or bottom - top < 4:
            raise StepError(EXIT_LOCATE_FAILED, "capture", f"截图区域无效：{rect}")
        raw = np.array(self.sct.grab({"left": left, "top": top,
                                      "width": right - left, "height": bottom - top}))
        return np.ascontiguousarray(raw[:, :, :3]), (left, top, right, bottom)

    def close(self):
        try:
            self.sct.close()
        except Exception:
            pass


class TemplateMatcher:
    """模板加载与多尺度匹配。"""

    def __init__(self, config: Dict, base_dir: str, logger: Logger):
        self.items = config["templates"]["items"]
        self.threshold = float(config["templates"]["threshold"])
        self.scales = [float(s) for s in config["templates"]["scales"]]
        self.template_dir = resolve_path(config["templates"]["dir"], base_dir)
        self.logger = logger
        self._scaled: Dict[Tuple[str, float], np.ndarray] = {}
        self._gray: Dict[str, np.ndarray] = {}

    def threshold_for(self, name: str) -> float:
        return float(self.items.get(name, {}).get("threshold", self.threshold))

    def template_path(self, name: str) -> str:
        item = self.items.get(name)
        if not item:
            raise StepError(EXIT_CONFIG, "template", f"未配置的模板名：{name}")
        return os.path.join(self.template_dir, item["file"])

    def template_variants(self, name: str) -> List[str]:
        """
        同一逻辑元素可能有两种外观（例如侧边栏图标在“未选中”时为灰色罗盘，
        选中后变为绿色对勾）。primary file 必填，alt_files 为可选备用模板。
        """
        item = self.items.get(name, {})
        files = [item.get("file")] + list(item.get("alt_files", []))
        paths = [os.path.join(self.template_dir, f) for f in files if f]
        return [p for p in paths if os.path.isfile(p)]

    def missing_required_templates(self) -> List[str]:
        missing = []
        for name, item in self.items.items():
            if item.get("optional"):
                continue
            if not os.path.isfile(os.path.join(self.template_dir, item["file"])):
                missing.append(f"{name} -> {item['file']}")
        return missing

    def load_gray(self, path: str) -> np.ndarray:
        if path in self._gray:
            return self._gray[path]
        data = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if data is None:
            raise StepError(EXIT_TEMPLATE_MISSING, "template", f"模板无法读取：{path}")
        gray = cv2.cvtColor(data, cv2.COLOR_BGR2GRAY)
        self._gray[path] = gray
        self.logger.debug("加载模板：%s 尺寸 %dx%d", path, gray.shape[1], gray.shape[0])
        return gray

    def best_match(self, name: str, scene_bgr: np.ndarray,
                   offset: Tuple[int, int] = (0, 0)) -> Optional[Match]:
        scene_gray = cv2.cvtColor(scene_bgr, cv2.COLOR_BGR2GRAY)
        best: Optional[Match] = None

        for path in self.template_variants(name):
            template_gray = self.load_gray(path)
            for scale in self.scales:
                key = (path, scale)
                if key not in self._scaled:
                    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
                    self._scaled[key] = cv2.resize(template_gray, None, fx=scale, fy=scale,
                                                   interpolation=interpolation)
                tpl = self._scaled[key]
                th, tw = tpl.shape[:2]
                sh, sw = scene_gray.shape[:2]
                if th > sh or tw > sw or th < 4 or tw < 4:
                    continue
                result = cv2.matchTemplate(scene_gray, tpl, cv2.TM_CCOEFF_NORMED)
                result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                self.logger.debug("  %s [%s] scale=%.2f score=%.4f loc=%s",
                                  name, os.path.basename(path), scale, max_val, max_loc)
                if best is None or max_val > best.score:
                    best = Match(name=name, score=float(max_val),
                                 left=int(max_loc[0]) + offset[0],
                                 top=int(max_loc[1]) + offset[1],
                                 width=tw, height=th, scale=scale)
        return best


# ============================================================================
# 七、主流程
# ============================================================================
@dataclass
class Content:
    text: str
    images: List[str]


class MomentsPoster:
    def __init__(self, config: Dict, logger: Logger, base_dir: str):
        self.cfg = config
        self.log = logger
        self.base_dir = base_dir
        self.timing = config["timing"]
        self.dry_run = bool(config["safety"]["dry_run"])
        self.grabber = ScreenGrabber()
        self.matcher = TemplateMatcher(config, base_dir, logger)
        self.log_dir = resolve_path(config["logging"]["dir"], base_dir)
        self.total_deadline = time.time() + float(self.timing["total_timeout_sec"])
        self.main_window: Optional[WindowInfo] = None
        self.wechat_pid: Optional[int] = None
        # 由 DIP 常量算出的“发表”按钮物理像素矩形 (x0,y0,x1,y1)，用于点击护栏
        self.publish_rect: Optional[Tuple[int, int, int, int]] = None
        # 客户区/窗口几何缓存（每次点击前刷新，窗口移动/缩放后自动跟随）
        self.geom: Optional[Dict] = None
        self._last_client_size: Optional[Tuple[int, int]] = None
        # 编辑面板打开时留存的“可见范围行”像素指纹，用于判断面板是否已关闭
        self.panel_fingerprint: Optional[np.ndarray] = None
        # 选图回车前可见范围行的像素（用于证明“回车后真的变成了编辑面板”）
        self._pre_enter_patch: Optional[np.ndarray] = None
        # 只做对照，不参与定位：打印常量预测点与模板实测中心的偏差
        self.debug_templates = bool(self.cfg["anchor"].get("debug_templates", False))
        # 发表前信息流首屏基准图 / 发表后首屏图（均永久留存到 logs/）
        self.feed_baseline: Optional[np.ndarray] = None
        self.feed_baseline_rect: Optional[Tuple[int, int, int, int]] = None
        self.feed_baseline_path: Optional[str] = None
        self.feed_after_path: Optional[str] = None
        # 是否处于定时发表模式（到点后跳过启动倒计时）
        self.scheduled = False
        # 本次是否用“一次多选导入”把 >=2 张图一次性放进编辑面板
        self._multi_import_used = False
        # 多图导入用的 staging 目录（按 01..09 命名保证文件管理器排序 = 参数顺序）
        self._staging_dir: Optional[str] = None
        self.content = self._load_content()
        # 编辑面板布局：<=2 张图是单行布局；>=3 张跳到固定的加高布局（实测二值跳变）
        layout_cfg = self.cfg["anchor"].get("editor_layout", {}) or {}
        min_multi = int(layout_cfg.get("multi_row_min_images", 3))
        self.layout_mode = "multi_row" if len(self.content.images) >= min_multi else "single_row"

    # ------------------------- 基础工具 -------------------------
    def _load_content(self) -> Content:
        content_cfg = self.cfg["content"]
        text = content_cfg.get("text", "") or ""
        text_file = content_cfg.get("text_file", "") or ""
        if text_file:
            path = resolve_path(text_file, self.base_dir)
            if not os.path.isfile(path):
                raise StepError(EXIT_CONFIG, "content", f"文字文件不存在：{path}")
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        images = [resolve_path(p, self.base_dir) for p in content_cfg.get("images", []) if p]
        for path in images:
            if not os.path.isfile(path):
                raise StepError(EXIT_CONFIG, "content", f"图片文件不存在：{path}")
        max_images = int((self.cfg["anchor"].get("editor_layout", {}) or {}).get("max_images", 9))
        if len(images) > max_images:
            raise StepError(EXIT_CONFIG, "content",
                            f"图片数量 {len(images)} 超过微信朋友圈单条上限 {max_images} 张；"
                            "已拒绝执行（不会把多出来的图片丢掉，也不会让客户端因超限报错）")
        if not text.strip() and not images:
            raise StepError(EXIT_CONFIG, "content", "文字与图片都为空，没有可发表的内容")
        return Content(text=text, images=images)

    def check_timeout(self, step: str):
        if time.time() > self.total_deadline:
            raise StepError(EXIT_TIMEOUT, step, "整体流程超时")

    def sleep_step(self):
        time.sleep(float(self.timing["step_wait_sec"]))

    def screenshot(self, tag: str) -> str:
        os.makedirs(self.log_dir, exist_ok=True)
        path = os.path.join(self.log_dir, f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.png")
        try:
            save_png(path, self.grabber.grab())
            self.log.info("已保存截图：%s", path)
        except Exception as exc:
            self.log.error("保存截图失败：%s", exc)
        return path

    # ------------------------- DIP 常量定位与效果验证 -------------------------
    def _anchor_cfg(self, name: str) -> Dict:
        items = self.cfg["anchor"]["items"]
        if name not in items:
            raise StepError(EXIT_CONFIG, "anchor", f"config.anchor 里没有锚点：{name}")
        return items[name]

    def _verify_cfg(self) -> Dict:
        return self.cfg["anchor"]["verify"]

    def _refresh_geometry(self) -> Dict:
        """
        重新读取窗口/客户区几何与 DPI 缩放。

        如果客户区尺寸与上次不同（窗口被拖动缩放），先等一会儿重绘再复核；
        复核仍在变化就报错退出——绝不在旧坐标上点击。
        """
        if not self.main_window:
            raise StepError(EXIT_WINDOW_NOT_FOUND, "geometry", "尚未定位微信主窗口")
        geom = window_geometry(self.main_window.hwnd)
        size = (geom["client"][2], geom["client"][3])
        if self._last_client_size and size != self._last_client_size:
            settle = float(self._verify_cfg().get("resize_settle_sec", 1.5))
            self.log.warning("检测到窗口客户区尺寸变化：%s -> %s，等待 %.1fs 重绘后重新测量",
                             self._last_client_size, size, settle)
            time.sleep(settle)
            geom = window_geometry(self.main_window.hwnd)
            size2 = (geom["client"][2], geom["client"][3])
            if size2 != size:
                raise StepError(EXIT_LOCATE_FAILED, "geometry",
                                f"窗口尺寸仍在变化（{size[0]}x{size[1]} -> "
                                f"{size2[0]}x{size2[1]}），已停止，不在旧坐标上点击")
        self._last_client_size = size
        self.geom = geom
        self.main_window.rect = geom["window"]
        self._refresh_publish_rect(geom)
        return geom

    def _anchor_point(self, name: str, geom: Optional[Dict] = None) -> Tuple[int, int]:
        """DIP 常量 -> 物理像素坐标（不写死物理像素，按实测 DPI 缩放换算）。"""
        geom = geom or self.geom or self._refresh_geometry()
        item = self._anchor_cfg(name)
        scale = float(geom["scale"])
        wl, wt, wr, wb = geom["window"]
        cx, cy, cw, ch = geom["client"]
        frame = item.get("frame", "client_tl")
        if frame == "client_tl":
            ox, oy = float(cx), float(cy)
        elif frame == "client_center":
            ox, oy = cx + cw / 2.0, cy + ch / 2.0
        elif frame == "window_right_top":
            ox, oy = float(wr), float(wt)
        else:
            raise StepError(EXIT_CONFIG, "anchor", f"未知参照系 frame={frame}（{name}）")
        dx, dy = self._anchor_dip(name, item)
        return int(round(ox + float(dx) * scale)), int(round(oy + float(dy) * scale))

    def _anchor_dip(self, name: str, item: Optional[Dict] = None) -> Tuple[float, float]:
        """按当前编辑面板布局取锚点 dip（variants.<layout_mode> 优先于默认 dip）。"""
        item = item or self._anchor_cfg(name)
        variant = (item.get("variants") or {}).get(self.layout_mode) or {}
        dip = variant.get("dip", item["dip"])
        return float(dip[0]), float(dip[1])

    def _anchor_size(self, name: str, item: Optional[Dict] = None) -> Tuple[float, float]:
        item = item or self._anchor_cfg(name)
        variant = (item.get("variants") or {}).get(self.layout_mode) or {}
        size = variant.get("size", item.get("size")) or [0.0, 0.0]
        return float(size[0]), float(size[1])

    def _anchor_box(self, name: str, pad_dip: float = 0.0,
                    geom: Optional[Dict] = None) -> Tuple[int, int, int, int]:
        """锚点元素的外接矩形（物理像素），尺寸同样由 DIP 常量 × 缩放换算。"""
        geom = geom or self.geom or self._refresh_geometry()
        item = self._anchor_cfg(name)
        x, y = self._anchor_point(name, geom)
        size = self._anchor_size(name, item)
        scale = float(geom["scale"])
        w = (float(size[0]) + 2.0 * pad_dip) * scale
        h = (float(size[1]) + 2.0 * pad_dip) * scale
        if item.get("origin") == "top_left":
            # dip 记录的是元素左上角（如文件对话框），不是中心
            return (int(round(x)), int(round(y)),
                    int(round(x + w)), int(round(y + h)))
        return (int(round(x - w / 2.0)), int(round(y - h / 2.0)),
                int(round(x + w / 2.0)), int(round(y + h / 2.0)))

    def _client_box(self, geom: Optional[Dict] = None) -> Tuple[int, int, int, int]:
        geom = geom or self.geom or self._refresh_geometry()
        cx, cy, cw, ch = geom["client"]
        return (cx, cy, cx + cw, cy + ch)

    def _refresh_publish_rect(self, geom: Optional[Dict] = None) -> None:
        """用常量算出「发表」按钮矩形（点击护栏的唯一来源）。"""
        self.publish_rect = self._anchor_box("editor_publish", 0.0, geom)
        if not getattr(self, "_publish_rect_logged", False):
            x0, y0, x1, y1 = self.publish_rect
            self.log.info("「发表」按钮护栏矩形（DIP 常量 / %s 布局）：x %d~%d, y %d~%d",
                          self.layout_mode, x0, x1, y0, y1)
            self._publish_rect_logged = True

    def _grab_patch(self, rect: Tuple[int, int, int, int]) -> np.ndarray:
        image, _actual = self.grabber.grab_rect(rect)
        return image

    def _diff_ratio(self, a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
        if a is None or b is None:
            return 0.0
        if a.shape[:2] != b.shape[:2]:
            return 1.0
        tol = int(self._verify_cfg().get("pixel_tolerance", 25))
        return gray_abs_diff_ratio(a, b, tol)

    @staticmethod
    def _patch_stats(patch: np.ndarray) -> Dict:
        if patch is None or patch.size == 0:
            return {"mean": 0.0, "std": 0.0, "dark": 0.0, "edge": 0.0, "green": 0.0}
        gray = to_gray(patch)
        b, g, r = (patch[:, :, 0].astype(int), patch[:, :, 1].astype(int),
                   patch[:, :, 2].astype(int))
        return {
            "mean": float(gray.mean()),
            "std": float(gray.std()),
            "dark": float(np.mean(gray < 128)),
            "edge": laplacian_var(gray),
            "green": float(np.mean((g > r + 25) & (g > b + 25))),
        }

    def _wait_effect(self, desc: str, probe, timeout: float,
                     step: str, retries_hint: str = "") -> bool:
        """轮询直到 probe() 返回 True；超时返回 False（由调用方抛错）。"""
        interval = float(self._verify_cfg().get("poll_interval_sec", 0.35))
        deadline = time.time() + float(timeout)
        last = "尚未采样"
        while time.time() < deadline:
            self.check_timeout(step)
            ok, last = probe()
            if ok:
                self.log.info("效果验证通过：%s（%s）", desc, last)
                return True
            time.sleep(interval)
        self.log.error("效果验证失败：%s（最后采样：%s）%s", desc, last, retries_hint)
        return False

    def _log_anchors(self) -> None:
        geom = self._refresh_geometry()
        cx, cy, cw, ch = geom["client"]
        self.log.info("几何：窗口外框=%s 客户区=(%d,%d) %dx%d DPI缩放=%.2f",
                      geom["window"], cx, cy, cw, ch, geom["scale"])
        self.log.info("客户区中心=(%d,%d)", cx + cw // 2, cy + ch // 2)
        self.log.info("编辑面板布局档位：%s（图片 %d 张）",
                      self.layout_mode, len(self.content.images))
        for name, item in self.cfg["anchor"]["items"].items():
            x, y = self._anchor_point(name, geom)
            dip = list(self._anchor_dip(name, item))
            self.log.info("锚点 %-20s %-17s dip=%-22s -> 物理(%4d,%4d)",
                          name, item.get("frame"), str(dip), x, y)

    def _debug_compare(self, name: str, point: Tuple[int, int]) -> None:
        """仅 --debug-templates：打印常量预测点与模板实测中心的偏差（不参与定位）。"""
        if not self.debug_templates:
            return
        if not self.matcher.template_variants(name):
            self.log.info("[模板对照] %s：templates 缺失，跳过（不影响流程）", name)
            return
        try:
            match = self.locate(name, timeout=2.0, required=False, save_fail_shot=False)
        except StepError as exc:
            self.log.warning("[模板对照] %s：定位异常 %s", name, exc.message)
            return
        if match is None:
            self.log.info("[模板对照] %s：模板未命中（界面状态不同），常量预测=(%d,%d)",
                          name, point[0], point[1])
            return
        dx, dy = match.center[0] - point[0], match.center[1] - point[1]
        self.log.info("[模板对照] %s：常量预测=(%d,%d) 模板实测=(%d,%d) 偏差=(%+d,%+d)",
                      name, point[0], point[1], match.center[0], match.center[1], dx, dy)

    # ------------------------- 信息流首屏取证 -------------------------
    def _feed_verify_cfg(self) -> Dict:
        return self.cfg["publish"].get("feed_verify", {}) or {}

    def _feed_rect(self) -> Tuple[int, int, int, int]:
        """首屏区域：相对微信主窗口的物理像素矩形（窗口移动后自动跟随）。"""
        roi = self._feed_verify_cfg().get("roi", [0.30, 0.05, 1.0, 1.0])
        base = self.main_window.rect if self.main_window else self.grabber.screen_rect
        left, top, right, bottom = base
        w, h = right - left, bottom - top
        x0, y0, x1, y1 = roi
        return (left + int(w * x0), top + int(h * y0),
                left + int(w * x1), top + int(h * y1))

    def _grab_feed_region(self) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
        return self.grabber.grab_rect(self._feed_rect())

    def _save_feed_image(self, tag: str, image: np.ndarray) -> str:
        """保存信息流截图，永久留存（不做任何清理/覆盖）。"""
        os.makedirs(self.log_dir, exist_ok=True)
        path = os.path.join(self.log_dir, f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.png")
        save_png(path, image)
        self.log.info("已保存信息流截图（永久留存）：%s", path)
        return path

    def _scroll_feed_to_top(self):
        """把鼠标移到信息流区域并向上滚动，确保首屏从最新一条开始。"""
        if self.dry_run:
            return
        cfg = self._feed_verify_cfg()
        x0, y0, x1, y1 = self._feed_rect()
        pyautogui.moveTo((x0 + x1) // 2, y0 + (y1 - y0) // 2, duration=0.2)
        pyautogui.scroll(int(cfg.get("scroll_units", 30)))
        time.sleep(0.4)

    def capture_feed_baseline(self):
        """
        进入编辑面板之前留存“发表前信息流首屏”基准图。

        这一步必须在点相机之前做：点完相机界面就被编辑面板/文件对话框盖住了，
        再也拿不到干净的基准首屏。
        """
        cfg = self._feed_verify_cfg()
        if not cfg.get("enabled", True):
            self.log.info("feed_verify 未启用，跳发表前基准图采集")
            return
        if self.dry_run:
            self.log.info("[DRY-RUN] 跳过信息流基准图采集")
            return
        if cfg.get("scroll_to_top", True):
            self._scroll_feed_to_top()
        time.sleep(float(cfg.get("settle_sec", 1.2)))
        image, rect = self._grab_feed_region()
        self.feed_baseline = image
        self.feed_baseline_rect = rect
        self.feed_baseline_path = self._save_feed_image("baseline_feed", image)
        self.log.info("发表前信息流首屏基准图：区域=%s", rect)

    def roi_rect(self, name: str) -> Tuple[int, int, int, int]:
        """
        计算模板的搜索区域：
          - roi 为 None：全屏搜索
          - roi 为 4 个 0~1 的数：相对微信主窗口（侧边栏/发现浮层都在主窗口内）
        """
        item = self.cfg["templates"]["items"][name]
        roi = item.get("roi")
        if not roi:
            return self.grabber.screen_rect
        base = self.main_window.rect if self.main_window else self.grabber.screen_rect
        left, top, right, bottom = base
        w, h = right - left, bottom - top
        nx0, ny0, nx1, ny1 = roi
        return (left + int(w * nx0), top + int(h * ny0),
                left + int(w * nx1), top + int(h * ny1))

    def locate(self, name: str, timeout: Optional[float] = None,
               required: bool = True, save_fail_shot: bool = True) -> Optional[Match]:
        """
        在 ROI 内反复截图定位。失败保存截图；required=True 抛 StepError。

        save_fail_shot=False：仅用于“探测式”调用（例如判断朋友圈是否已打开），
        避免每次探测失败都写一张失败截图。
        """
        self.check_timeout(f"locate:{name}")
        if cv2 is None:
            msg = ("当前运行时未包含 OpenCV，模板匹配不可用；"
                   "运行时定位由 DIP 常量完成，不依赖模板")
            if required:
                raise StepError(EXIT_TEMPLATE_MISSING, f"locate:{name}", msg)
            self.log.debug("模板匹配不可用（%s），跳过：%s", msg, name)
            return None
        path = self.matcher.template_path(name)
        if not os.path.isfile(path):
            if self.cfg["templates"]["items"].get(name, {}).get("optional") or not required:
                self.log.debug("可选模板不存在，跳过：%s", name)
                return None
            raise StepError(EXIT_TEMPLATE_MISSING, f"locate:{name}",
                            f"缺少模板文件 {path}，请用 capture_template.py 采集")

        timeout = float(timeout if timeout is not None else self.timing["locate_timeout_sec"])
        interval = float(self.timing["locate_retry_interval_sec"])
        deadline = time.time() + timeout
        attempt = 0
        last_score = 0.0
        threshold = self.matcher.threshold_for(name)

        while time.time() < deadline:
            self.check_timeout(f"locate:{name}")
            attempt += 1
            try:
                image, actual = self.grabber.grab_rect(self.roi_rect(name))
            except StepError:
                raise
            match = self.matcher.best_match(name, image, offset=(actual[0], actual[1]))
            if match:
                last_score = max(last_score, match.score)
                if match.score >= threshold:
                    self.log.info("定位成功：%s 得分=%.3f 中心=(%d,%d) 缩放=%.2f",
                                  name, match.score, match.center[0], match.center[1],
                                  match.scale)
                    return match
            time.sleep(interval)

        # “探测式”调用（required=False）属于正常情况（例如先确认编辑面板是否已出现），
        # 只在 DEBUG 里留痕，避免日志里出现误导性的 ERROR。
        level_log = self.log.error if required else self.log.debug
        level_log("定位失败：%s（尝试 %d 次，最高得分 %.4f，阈值 %.2f）",
                  name, attempt, last_score, threshold)
        if save_fail_shot:
            self.screenshot(f"fail_{name}")
        if required:
            desc = self.cfg["templates"]["items"][name].get("desc", name)
            raise StepError(EXIT_LOCATE_FAILED, f"locate:{name}",
                            f"未能在 {timeout:.1f}s 内定位到「{desc}」")
        return None

    def _is_wechat_window(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        try:
            root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd
            pid = win32process.GetWindowThreadProcessId(root)[1]
        except Exception:
            return False
        return self.wechat_pid is not None and pid == self.wechat_pid

    def _wechat_windows(self) -> List[int]:
        """枚举微信进程的可见顶层窗口（主窗口 + “选择文件”对话框等）。"""
        result: List[int] = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if not win32gui.GetWindowText(hwnd):
                return True
            if self._is_wechat_window(hwnd):
                result.append(hwnd)
            return True

        win32gui.EnumWindows(cb, None)
        return result

    def _ensure_foreground(self, step: str = "foreground") -> None:
        """
        确保“微信的某个窗口”在最前面（主窗口或它弹出的“选择文件”对话框），
        否则拒绝任何点击/粘贴。

        注意：不能只判断主窗口——选图时前台是对话框（同进程不同 hwnd）。
        """
        if not self.main_window:
            raise StepError(EXIT_WINDOW_NOT_FOUND, step, "尚未定位微信主窗口")
        if self._is_wechat_window(win32gui.GetForegroundWindow()):
            return
        self.log.info("微信不在前台，正在重新前置……")
        for hwnd in [self.main_window.hwnd] + self._wechat_windows():
            if activate_window(hwnd) and self._is_wechat_window(win32gui.GetForegroundWindow()):
                return
        self.screenshot("fail_foreground")
        raise StepError(EXIT_LOCATE_FAILED, step,
                        "微信窗口无法切到前台（被其他窗口遮挡），已拒绝按坐标点击/粘贴")

    def _assert_click_target(self, x: int, y: int, desc: str, step: str) -> None:
        """
        点击前确认 (x,y) 命中的窗口属于微信进程。

        这条校验防止“微信被 IDE/浏览器等窗口挡住时，脚本把坐标点到了别的程序”。
        """
        hwnd = win32gui.WindowFromPoint((x, y))
        if not hwnd:
            raise StepError(EXIT_LOCATE_FAILED, step,
                            f"「{desc}」目标点 ({x},{y}) 没有窗口，已拒绝点击")
        try:
            root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd
        except Exception:
            root = hwnd
        try:
            pid = win32process.GetWindowThreadProcessId(root)[1]
        except Exception:
            pid = None
        if self.wechat_pid is not None and pid != self.wechat_pid:
            raise StepError(EXIT_LOCATE_FAILED, step,
                            f"「{desc}」目标点 ({x},{y}) 当前属于其它程序窗口"
                            f"（hwnd={root}, pid={pid}），已拒绝点击")

    def _guard(self, x: int, y: int, desc: str, allow_publish: bool) -> None:
        """
        点击护栏：除“发表”步骤本身（allow_publish=True），任何点击一旦落在
        由常量算出的「发表」按钮矩形内，一律拒绝执行（退出码 10）。
        """
        if allow_publish or self.publish_rect is None:
            return
        x0, y0, x1, y1 = self.publish_rect
        if x0 <= x <= x1 and y0 <= y <= y1:
            raise StepError(EXIT_SAFETY_BLOCKED, "click_guard",
                            f"拒绝点击「{desc}」({x},{y})：该坐标落在「发表」按钮"
                            f"{self.publish_rect} 内。已中止，未做任何点击")

    def _click_xy(self, x: int, y: int, desc: str, allow_publish: bool = False,
                  move_duration: float = 0.2):
        """按 DIP 常量算出的物理坐标点击（带护栏）。"""
        self._guard(x, y, desc, allow_publish)
        if self.dry_run:
            self.log.info("[DRY-RUN] 「%s」目标坐标 (%d,%d)，未点击", desc, x, y)
            try:
                pyautogui.moveTo(x, y, duration=move_duration)
            except pyautogui.FailSafeException:
                raise UserAbort("鼠标移到屏幕角落，FailSafe 中止")
            return
        # 前置 + 命中窗口归属校验：防止微信被别的窗口挡住时点错程序
        self._ensure_foreground("click")
        self._assert_click_target(x, y, desc, "click")
        self.log.info("点击「%s」(%d,%d)", desc, x, y)
        try:
            pyautogui.moveTo(x, y, duration=move_duration)
            pyautogui.click()
        except pyautogui.FailSafeException:
            raise UserAbort("鼠标移到屏幕角落，FailSafe 中止")

    def click(self, match: Match, desc: str, move_duration: float = 0.2,
              allow_publish: bool = False):
        """点击模板匹配到的中心点（仅用于可选的二次确认兜底）。"""
        self._click_xy(match.center[0], match.center[1], desc,
                       allow_publish=allow_publish, move_duration=move_duration)

    def paste(self, text: str, desc: str):
        """剪贴板 + Ctrl+V，支持中文与特殊字符。"""
        if self.dry_run:
            self.log.info("[DRY-RUN] 本应粘贴%s：%s", desc, text.replace("\n", "\\n")[:80])
            return
        self._ensure_foreground("paste")   # 粘贴也必须发生在微信前台
        old_clipboard = ""
        try:
            old_clipboard = pyperclip.paste()
        except Exception:
            pass
        try:
            pyperclip.copy(text)
        except Exception as exc:
            raise StepError(EXIT_LOCATE_FAILED, "clipboard", f"写入剪贴板失败：{exc}")
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.2)
        self.log.info("已粘贴%s（%d 字符）", desc, len(text))
        try:
            if old_clipboard:
                pyperclip.copy(old_clipboard)
        except Exception:
            pass

    def wait_window(self, pattern: str, timeout: float,
                    exclude_hwnd: Optional[int] = None) -> Optional[WindowInfo]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.check_timeout("wait_window")
            win = find_window(pattern, exclude_hwnd)
            if win:
                return win
            time.sleep(0.4)
        return None

    # ------------------------- 步骤 -------------------------
    def step_countdown(self):
        seconds = int(self.timing["countdown_sec"])
        if self.scheduled:
            self.log.info("定时模式：已到点，直接执行，跳过启动倒计时")
            return
        if self.dry_run:
            self.log.info("[DRY-RUN] 跳过倒计时")
            return
        if seconds <= 0:
            return
        self.log.info("将在 %d 秒后开始操作：请勿触碰鼠标键盘；"
                      "把鼠标移到屏幕任意角落可立即中止", seconds)
        for left in range(seconds, 0, -1):
            try:
                pos = pyautogui.position()
                w, h = pyautogui.size()
                if pos.x <= 1 or pos.y <= 1 or pos.x >= w - 2 or pos.y >= h - 2:
                    raise UserAbort("倒计时期间鼠标位于屏幕角落，已中止")
            except pyautogui.FailSafeException:
                raise UserAbort("倒计时期间触发 FailSafe，已中止")
            print(f"\r倒计时 {left} 秒... ", end="", flush=True)
            time.sleep(1)
        print("\r开始执行            ")

    def step_find_main_window(self):
        self.check_timeout("main_window")
        pattern = self.cfg["windows"]["main"]["title_regex"]
        window = find_window(pattern)
        if not window:
            self.screenshot("fail_main_window")
            raise StepError(EXIT_WINDOW_NOT_FOUND, "main_window",
                            f"未找到标题匹配 {pattern} 的微信主窗口，请确认微信已登录")
        self.main_window = window
        try:
            self.wechat_pid = win32process.GetWindowThreadProcessId(window.hwnd)[1]
        except Exception:
            self.wechat_pid = None
        self.log.info("微信主窗口：hwnd=%s 标题=%s 位置=%s 尺寸=%dx%d pid=%s",
                      window.hwnd, window.title, window.rect,
                      window.rect[2] - window.rect[0], window.rect[3] - window.rect[1],
                      self.wechat_pid)
        if not activate_window(window.hwnd):
            self.screenshot("fail_foreground")
            raise StepError(EXIT_WINDOW_NOT_FOUND, "main_window",
                            "无法把微信主窗口切到前台（可能被其他窗口遮挡或前台锁限制），"
                            "已停止：绝不在看不到微信的情况下按坐标点击")
        time.sleep(float(self.timing["step_wait_sec"]))
        refreshed = find_window(pattern)
        if refreshed:
            self.main_window = refreshed
        # 记录基线几何 + 计算所有锚点 + 用常量算出「发表」护栏矩形
        self._log_anchors()
        self.log.debug("主窗口最终位置：%s", self.main_window.rect)

    # ---------------- 效果验证判据（全部基于像素取样，不依赖模板） ----------------
    def _discover_visible(self, allow_selected: bool = False) -> Tuple[bool, str]:
        """
        “发现”浮层是否已展开（在浮层常量位置附近取样）。

        两种真实外观都算“浮层出现”：
          * 浅色面板（行未被选中）：mean 高、dark 低；
          * 绿色选中行（点侧边栏「朋友圈」图标后该行高亮）：green 高。
            该外观在聊天页（第一条会话被选中）也可能出现，因此默认不接受，
            只有 allow_selected=True（点击之后 + 已确认该区域发生变化）时才认。
        """
        cfg = self._verify_cfg()["discover"]
        try:
            patch = self._grab_patch(self._anchor_box("discover_moments"))
        except StepError as exc:
            return False, f"取样失败：{exc.message}"
        s = self._patch_stats(patch)
        white = (s["mean"] >= float(cfg.get("min_mean", 200))
                 and s["dark"] <= float(cfg.get("max_dark", 0.20))
                 and s["edge"] >= float(cfg.get("min_edge", 600)))
        selected = (allow_selected
                    and s["green"] >= float(cfg.get("min_green_row", 0.30))
                    and s["edge"] >= float(cfg.get("min_edge", 600)))
        ok = white or selected
        detail = (f"mean={s['mean']:.1f} std={s['std']:.1f} dark={s['dark']:.3f} "
                  f"edge={s['edge']:.0f} green={s['green']:.3f} "
                  f"形态={'浅色面板' if white else ('绿色选中行' if selected else '无')}")
        return ok, detail

    def _camera_content(self) -> Tuple[bool, str]:
        """相机位置附近是否有“内容”（封面/信息流已渲染，而不是纯白加载占位）。"""
        cfg = self._verify_cfg()["moments"]
        try:
            s = self._patch_stats(self._grab_patch(self._anchor_box("moments_camera")))
        except StepError as exc:
            return False, f"取样失败：{exc.message}"
        ok = (s["std"] >= float(cfg.get("min_std", 8))
              and s["dark"] >= float(cfg.get("min_dark", 0.01)))
        return ok, f"相机区 std={s['std']:.1f} dark={s['dark']:.3f}"

    def _moments_ready_sample(self, cam_ref: np.ndarray,
                              client_ref: np.ndarray) -> Tuple[bool, str]:
        """
        朋友圈页就绪判据：
          1) 相机位置附近“有内容”（加载占位是纯白，std/dark 都≈0，必须排除）；
          2) 整块客户区相对点击前确实变了（证明点完真的重绘了）。
        注意：不再接受“只有变化、没有内容”的宽松分支——实测加载中的空白页
        会让“变化”判据假通过，然后在旧坐标上点相机，点空。
        """
        cfg = self._verify_cfg()["moments"]
        try:
            cam = self._grab_patch(self._anchor_box("moments_camera"))
            client = self._grab_patch(self._client_box())
        except StepError as exc:
            return False, f"取样失败：{exc.message}"
        cam_stats = self._patch_stats(cam)
        cam_diff = self._diff_ratio(cam_ref, cam)
        page_diff = self._diff_ratio(client_ref, client)
        content = (cam_stats["std"] >= float(cfg.get("min_std", 8))
                   and cam_stats["dark"] >= float(cfg.get("min_dark", 0.01)))
        changed_page = page_diff >= float(cfg.get("min_page_diff", 0.05))
        ok = content and changed_page
        detail = (f"相机区 std={cam_stats['std']:.1f} dark={cam_stats['dark']:.3f} "
                  f"diff={cam_diff:.2f}；客户区 diff={page_diff:.2f}")
        return ok, detail

    def _editor_probe_name(self) -> str:
        """
        编辑面板“就绪 / 已关闭”的判据区域。

        单行布局（1~2 张图）：用“谁可以看 … 公开”整行（浅底 + 有文字）。
        多行布局（3~9 张图）：该行文本消失，改用绿色「发表」按钮的绿色占比。
        """
        return "editor_publish" if self.layout_mode == "multi_row" else "editor_visibility"

    def _editor_probe_patch(self) -> np.ndarray:
        return self._grab_patch(self._anchor_box(self._editor_probe_name()))

    def _editor_present(self) -> Tuple[bool, str]:
        """编辑面板是否已出现（判据区域随布局切换，见 _editor_probe_name）。"""
        cfg = self._verify_cfg()["editor"]
        name = self._editor_probe_name()
        try:
            patch = self._grab_patch(self._anchor_box(name))
        except StepError as exc:
            return False, f"取样失败：{exc.message}"
        s = self._patch_stats(patch)
        if name == "editor_publish":
            ok = s["green"] >= float(cfg.get("multi_min_green", 0.50))
            return ok, (f"「发表」按钮绿色占比={s['green']:.3f} mean={s['mean']:.1f} "
                        f"std={s['std']:.1f}（多行布局判据）")
        ok = (s["mean"] >= float(cfg.get("min_mean", 150))
              and s["std"] >= float(cfg.get("min_std", 5))
              and float(cfg.get("min_dark", 0.002)) <= s["dark"] <= float(cfg.get("max_dark", 0.55)))
        return ok, (f"mean={s['mean']:.1f} std={s['std']:.1f} dark={s['dark']:.3f} "
                    f"edge={s['edge']:.0f}")

    def _wait_editor_ready(self, timeout: float,
                           ref_patch: Optional[np.ndarray] = None) -> bool:
        """
        等待编辑面板就绪。

        ref_patch 不为空时，还要求“可见范围行”相对该参考图发生明显变化
        （即回车之后界面真的换成了编辑面板，而不是本来就像面板）。
        就绪时留存面板指纹，供“面板是否已关闭”的后续校验使用。
        """
        cfg = self._verify_cfg()["editor"]
        min_diff = float(cfg.get("min_diff", 0.10))

        def probe():
            ok, detail = self._editor_present()
            if not ok:
                return False, detail
            if ref_patch is not None:
                cur = self._editor_probe_patch()
                diff = self._diff_ratio(ref_patch, cur)
                if diff < min_diff:
                    return False, f"{detail}；相对回车前变化={diff:.2f} < {min_diff:.2f}"
                detail = f"{detail}；相对回车前变化={diff:.2f}"
            return True, detail

        ok = self._wait_effect("编辑面板已出现（%s 布局判据区域有内容）" % self.layout_mode,
                               probe, timeout, "editor_ready")
        if ok:
            try:
                self.panel_fingerprint = self._editor_probe_patch()
            except StepError:
                self.panel_fingerprint = None
        return ok

    def _panel_closed(self, use_feed_baseline: bool = False) -> Tuple[bool, str]:
        """
        编辑面板是否已关闭。

        判据 = 可见范围行相对“面板指纹”发生明显变化。
        use_feed_baseline=True 时（清理草稿场景，信息流本身没变）额外要求
        该区域重新看起来像“发表前的信息流首屏”——用来区分
        “面板真的关闭了”和“面板还在、只是被保留确认框遮暗了”。
        """
        cfg = self._verify_cfg()["panel_closed"]
        if self.panel_fingerprint is None:
            return False, "缺少面板指纹，无法判断"
        probe_name = self._editor_probe_name()
        try:
            patch = self._grab_patch(self._anchor_box(probe_name))
        except StepError as exc:
            return False, f"取样失败：{exc.message}"
        diff = self._diff_ratio(self.panel_fingerprint, patch)
        ok = diff >= float(cfg.get("min_diff", 0.12))
        detail = f"{probe_name} 相对面板指纹变化={diff:.2f}"
        if use_feed_baseline:
            base = self._baseline_patch(probe_name)
            if base is None:
                detail += "；无信息流基准图（仅按指纹判断）"
            else:
                diff_base = self._diff_ratio(base, patch)
                ok = ok and diff_base <= float(cfg.get("max_feed_diff", 0.30))
                detail += f"；相对信息流首屏变化={diff_base:.2f}"
        return ok, detail

    def _baseline_patch(self, name: str) -> Optional[np.ndarray]:
        """从发表前信息流基准图里裁出指定锚点矩形（越界返回 None）。"""
        rect = getattr(self, "feed_baseline_rect", None)
        if self.feed_baseline is None or not rect:
            return None
        bx0, by0, bx1, by1 = rect
        x0, y0, x1, y1 = self._anchor_box(name)
        if not (bx0 <= x0 and by0 <= y0 and x1 <= bx1 and y1 <= by1):
            return None
        return self.feed_baseline[y0 - by0:y1 - by0, x0 - bx0:x1 - bx0]

    def _moments_page_active(self) -> Tuple[bool, str]:
        """
        朋友圈页面是否**已经**打开（用于复用，避免重复点击侧面图标）。

        判据（三个信号同时成立）：
          1) 侧边栏「朋友圈」图标是绿色（激活态）；
          2) 相机位置附近有内容（封面图/信息流，不是空白）；
          3) “发现”浮层区域不是浅色面板（说明浮层没展开，页面本身在前台）。
        这条判据在未打开朋友圈（聊天页）时不成立：图标是灰色。
        """
        sb = self._patch_stats(self._grab_patch(self._anchor_box("sidebar_moments")))
        cam = self._patch_stats(self._grab_patch(self._anchor_box("moments_camera")))
        discover_open, _ = self._discover_visible()
        active = (sb["green"] >= 0.10
                  and cam["std"] >= float(self._verify_cfg()["moments"].get("min_std", 8))
                  and cam["dark"] >= float(self._verify_cfg()["moments"].get("min_dark", 0.01))
                  and not discover_open)
        detail = (f"侧边栏绿色={sb['green']:.3f}；相机区 std={cam['std']:.1f} "
                  f"dark={cam['dark']:.3f}；浮层浅色面板={discover_open}"
                  f"（若浮层行是绿色选中态，这里为 False，但相机点击不受遮挡）")
        return active, detail

    def step_open_moments(self):
        """
        打开朋友圈页面（全程 DIP 常量点击 + 每步效果验证）。

        实测结论（微信 4.2.0.13 / 150% 缩放）：
        点击左侧「朋友圈」图标 → 弹出“发现”浮层 → 点击浮层里的「朋友圈」行之后，
        朋友圈页面直接显示在主窗口内，不一定会新开标题为“朋友圈”的独立窗口。
        因此以“相机位置附近有内容 + 客户区确实重绘”作为页面就绪判据。
        """
        self.check_timeout("open_moments")
        cfg_disc = self._verify_cfg()["discover"]
        cfg_cam = self._verify_cfg()["moments"]
        retries = int(self.timing.get("moments_open_retries", 2))

        for attempt in range(1, retries + 1):
            self.check_timeout("open_moments")
            self._refresh_geometry()

            # 0) 朋友圈页面已打开 → 直接复用（实测：此时再点侧边栏图标不会弹浮层）
            active, detail = self._moments_page_active()
            if active:
                self.log.info("朋友圈页面已打开，复用当前页面（%s）", detail)
                return

            # 1) 浮层是否已展开（像素判据：浅色面板 + 行内文字）
            opened, detail = self._discover_visible()
            if opened:
                self.log.info("“发现”浮层已展开（%s），跳过侧边栏点击", detail)
            else:
                disc_box = self._anchor_box("discover_moments")
                before = self._grab_patch(disc_box)
                sx, sy = self._anchor_point("sidebar_moments")
                self._debug_compare("sidebar_moments", (sx, sy))
                self._click_xy(sx, sy, "左侧导航栏「朋友圈」图标")
                if self.dry_run:
                    raise DryRunStop("dry-run：已完成全部锚点换算；真实点击才会展开“发现”浮层")

                def probe_discover():
                    cur = self._grab_patch(disc_box)
                    diff = self._diff_ratio(before, cur)
                    # 点击后允许“绿色选中行”形态（点侧边栏图标会把浮层里的行高亮）
                    ok, sig = self._discover_visible(allow_selected=True)
                    return (ok and diff >= float(cfg_disc.get("min_diff", 0.15)),
                            f"浮层区变化={diff:.2f}；{sig}")

                if not self._wait_effect("侧边栏点击后“发现”浮层出现", probe_discover,
                                         float(cfg_disc.get("timeout_sec", 5.0)),
                                         "open_moments"):
                    self.screenshot("fail_discover_panel")
                    raise StepError(EXIT_LOCATE_FAILED, "open_moments",
                                    "点击左侧「朋友圈」图标后，浮层位置附近没有出现“发现”浮层"
                                    "（像素判据未通过），已停止，未做后续点击")

            # 2) 点击浮层里的「朋友圈」行
            #    实测：点侧边栏图标本身就已经开始切朋友圈页（页面在加载中），
            #    在“加载态”上再点浮层行会把客户端卡在加载遮罩里（本次实测复现），
            #    所以先等页面内容出现（短超时、非致命），再点这一行做确认。
            settle = float(cfg_cam.get("settle_timeout_sec", 6.0))
            if not self._wait_effect("侧边栏点击后朋友圈页内容出现（避免在加载态点浮层行）",
                                     self._camera_content, settle, "moments_settle",
                                     "（超时也会继续，由后面的就绪判据兜底）"):
                self.log.warning("侧边栏点击后 %.1f 秒内未见页面内容，仍继续点浮层行确认", settle)

            rx, ry = self._anchor_point("discover_moments")
            self._debug_compare("discover_moments", (rx, ry))
            cam_box = self._anchor_box("moments_camera")
            cam_before = self._grab_patch(cam_box)
            client_before = self._grab_patch(self._client_box())
            self._click_xy(rx, ry, "“发现”浮层「朋友圈」")
            if self.dry_run:
                raise DryRunStop("dry-run：不执行浮层行点击")

            # 3) 等朋友圈页面就绪（相机位置附近有内容 + 页面变化）
            if self._wait_effect("点击「朋友圈」后页面就绪",
                                 lambda: self._moments_ready_sample(cam_before, client_before),
                                 float(cfg_cam.get("timeout_sec", 20.0)), "moments_ready"):
                self.log.info("朋友圈页面已就绪")
                return
            self.screenshot(f"fail_moments_loading_{attempt}")
            self.log.warning("第 %d/%d 次点击「朋友圈」后页面仍未就绪（可能仍在加载）",
                             attempt, retries)
            # 重新展开浮层后再试
            if attempt < retries:
                time.sleep(float(self.timing["step_wait_sec"]))

        raise StepError(EXIT_LOCATE_FAILED, "open_moments",
                        "多次点击「朋友圈」后页面仍未就绪，请检查微信/网络状态")

    def step_open_publish_editor(self):
        """
        点击相机进入发表流程：
          - 若直接出现编辑面板（部分版本）：继续
          - 若先出现“选择文件”对话框（本机 4.2 实测）：必须有图片，选第一张
        """
        self.check_timeout("open_editor")
        # 关键：点相机之前先留存“发表前信息流首屏”基准图（之后界面就被盖住了）
        self.capture_feed_baseline()
        self._refresh_geometry()
        cx, cy = self._anchor_point("moments_camera")
        self._debug_compare("moments_camera", (cx, cy))
        self._click_xy(cx, cy, "朋友圈相机（发表）图标")
        if self.dry_run:
            raise DryRunStop("dry-run：不执行相机点击（编辑面板/对话框需要真实点击才会出现）")

        # 点击相机后两种可能：直接出现编辑面板，或先弹“选择文件”对话框。
        dialog_pattern = self.cfg["windows"]["file_dialog"]["title_regex"]
        deadline = time.time() + float(self.timing["dialog_timeout_sec"])
        dialog = None
        while time.time() < deadline:
            self.check_timeout("open_editor")
            dialog = find_window(dialog_pattern)
            if dialog:
                break
            if self._editor_present()[0]:
                self.log.info("效果验证通过：相机点击后直接进入编辑面板")
                self._wait_editor_ready(1.0)
                return
            time.sleep(0.3)

        if dialog is None:
            self.screenshot("fail_after_camera")
            raise StepError(EXIT_LOCATE_FAILED, "open_editor",
                            "点击相机后既没有出现编辑面板，也没有出现文件选择对话框"
                            "（效果验证未通过），已停止")

        # 验证通过：文件对话框确实出现（按窗口标题确认），并记录与常量的偏差
        self.log.info("效果验证通过：文件对话框已出现，标题=%s", dialog.title)
        self._log_dialog_deviation(dialog)

        # 本机 4.2.0.13 实测路径：先选图片，选完才出现编辑面板
        if not self.content.images:
            self.screenshot("fail_no_image")
            raise StepError(EXIT_CONFIG, "content",
                            "当前微信版本点击相机会先要求选择图片（本机 4.2.0.13 实测）。"
                            "请在配置中提供至少一张图片：content.images 或 --image 参数")
        image_count = len(self.content.images)
        if image_count == 1:
            self._pick_file(dialog, self.content.images[0], 1)
        else:
            # >=2 张：一次多选导入（粘贴多个路径会被文件名框长度上限截断）
            staging = self._stage_images()
            self._pick_files_multi(dialog, staging, image_count)
            self._multi_import_used = True
        if not self._wait_editor_ready(timeout=float(self.timing["editor_open_timeout_sec"]),
                                       ref_patch=self._pre_enter_patch):
            self.screenshot("fail_editor_not_ready")
            raise StepError(EXIT_LOCATE_FAILED, "open_editor",
                            "选择图片并回车后编辑面板未出现（%s 布局判据未通过），已停止"
                            % self.layout_mode)
        self.log.info("编辑面板已就绪（%s 布局）", self.layout_mode)

        if self._multi_import_used:
            ok, detail = self._verify_grid(image_count)
            self.log.info("图片网格完整性校验：%s；%s", "通过" if ok else "未通过", detail)
            if not ok:
                self.screenshot("fail_grid_incomplete")
                raise StepError(EXIT_DIALOG_FAILED, "open_editor",
                                f"编辑面板图片网格与预期 {image_count} 张不一致（{detail}），"
                                "已停止，未输入文字也未发表")

    def _log_dialog_deviation(self, dialog: WindowInfo) -> None:
        """记录文件对话框实测外框与常量的偏差（只记日志，不影响交互）。"""
        geom = self.geom or self._refresh_geometry()
        px0, py0, px1, py1 = self._anchor_box("file_dialog", 0.0, geom)
        dl, dt, dr, db = dialog.rect
        dx, dy = dl - px0, dt - py0
        dw, dh = (dr - dl) - (px1 - px0), (db - dt) - (py1 - py0)
        self.log.info("文件对话框实测外框=%s 常量预测=(%d,%d,%d,%d) "
                      "位置偏差=(%+d,%+d) 尺寸偏差=(%+d,%+d)",
                      dialog.rect, px0, py0, px1, py1, dx, dy, dw, dh)
        if max(abs(dx), abs(dy), abs(dw), abs(dh)) > 40:
            self.log.warning("文件对话框与常量偏差较大；实际交互仍以窗口 hwnd 为准"
                             "（Alt+N + 粘贴路径 + Enter），不依赖该坐标")

    def step_input_text(self):
        if not self.content.text.strip():
            self.log.info("未配置文字，跳过文字输入")
            return
        self.check_timeout("input_text")
        self._refresh_geometry()
        ix, iy = self._anchor_point("editor_input")
        self._debug_compare("editor_input", (ix, iy))
        self._click_xy(ix, iy, "文字输入区")
        if self.dry_run:
            self.log.info("[DRY-RUN] 跳过文字粘贴")
            return
        time.sleep(0.2)
        self.paste(self.content.text, "文字内容")
        self.sleep_step()

    def step_add_more_images(self):
        """
        补齐图片。

        现在 >=2 张会走“一次多选导入”（见 _pick_files_multi），编辑面板打开时
        已经一次性放好了全部图片，本步骤不再需要点“+”；保留单张续加路径仅用于
        多选导入未发生的情况（例如将来需要逐张追加时）。
        """
        if self._multi_import_used:
            self.log.info("图片已通过一次多选导入（%d 张），跳过逐张“+”添加",
                          len(self.content.images))
            return
        extra = self.content.images[1:]
        if not extra:
            return
        for index, image_path in enumerate(extra, start=2):
            self.check_timeout("add_image")
            self._refresh_geometry()
            ax, ay = self._anchor_point("editor_add_image")
            self._debug_compare("editor_add_image", (ax, ay))
            self._click_xy(ax, ay, f"添加图片（第 {index} 张）")
            if self.dry_run:
                self.log.info("[DRY-RUN] 跳过文件对话框操作")
                return
            dialog = self.wait_window(self.cfg["windows"]["file_dialog"]["title_regex"],
                                      timeout=float(self.timing["dialog_timeout_sec"]))
            if not dialog:
                self.screenshot(f"fail_dialog_{index}")
                raise StepError(EXIT_DIALOG_FAILED, "add_image",
                                f"点击“+”后未出现文件选择对话框（第 {index} 张）"
                                "（效果验证未通过）")
            self._pick_file(dialog, image_path, index)
            if not self._wait_editor_ready(timeout=float(self.timing["editor_open_timeout_sec"]),
                                           ref_patch=self._pre_enter_patch):
                self.screenshot(f"fail_editor_after_image_{index}")
                raise StepError(EXIT_DIALOG_FAILED, "add_image",
                                f"添加第 {index} 张图片后编辑面板未恢复（效果验证未通过）")
        self.sleep_step()

    def _pick_file(self, dialog: WindowInfo, image_path: str, index: int):
        """
        文件选择对话框只用键鼠模拟：
          Alt+N 聚焦“文件名”输入框 -> 全选清空 -> Ctrl+V 粘贴绝对路径 -> Enter 确认。
        不读取控件树，也不依赖对话框控件 ID。
        对话框偶发“焦点没落在文件名框 / 粘贴被忽略”，因此失败后会整段重试。
        """
        pattern = self.cfg["windows"]["file_dialog"]["title_regex"]
        attempts = int(self.timing.get("dialog_confirm_retries", 3))
        for attempt in range(1, attempts + 1):
            self.check_timeout("file_dialog")
            self.log.info("文件对话框（第 %d/%d 次尝试）：标题=%s hwnd=%s",
                          attempt, attempts, dialog.title, dialog.hwnd)
            activate_window(dialog.hwnd, retries=3)
            time.sleep(0.5)
            pyautogui.hotkey("alt", "n")   # 聚焦“文件名”输入框
            time.sleep(0.3)
            pyautogui.hotkey("ctrl", "a")  # 清空输入框里可能已有的内容
            time.sleep(0.1)
            self.paste(image_path, f"第 {index} 张图片路径")
            time.sleep(0.5)
            if self._pre_enter_patch is None or index <= 2:
                # 回车前留存判据区域（之后要证明这里真的变成了编辑面板）
                self._pre_enter_patch = self._editor_probe_patch()
            pyautogui.press("enter")       # 等价于点击“打开”
            if self._wait_dialog_closed(pattern, timeout=3.0):
                self.log.info("已在对话框确认图片：%s", image_path)
                return
            self.log.warning("文件对话框仍未关闭（第 %d/%d 次尝试），重试", attempt, attempts)

        self.screenshot(f"fail_dialog_close_{index}")
        raise StepError(EXIT_DIALOG_FAILED, "file_dialog",
                        "文件对话框未关闭，可能路径无效或对话框焦点异常")

    def _wait_dialog_closed(self, pattern: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not find_window(pattern):
                return True
            time.sleep(0.3)
        return False

    # ------------------------- 多图一次性导入 + 网格校验 -------------------------
    def _stage_images(self) -> str:
        """
        把 content.images 按参数顺序复制到 staging 目录，文件名固定为 01..09。

        Windows 文件对话框“全选”按文件名升序导入，前缀数字保证：
        编辑面板里的图片顺序 == 参数里的图片顺序（与源文件名无关）。
        """
        staging = os.path.join(self.log_dir, "staging_%s" % time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(staging, exist_ok=True)
        for index, src in enumerate(self.content.images, start=1):
            ext = os.path.splitext(src)[1].lower() or ".jpg"
            shutil.copyfile(src, os.path.join(staging, "%02d%s" % (index, ext)))
        self._staging_dir = staging
        self.log.info("多图 staging 目录：%s（%d 张，按 01..%02d 命名保证顺序）",
                      staging, len(self.content.images), len(self.content.images))
        return staging

    def _pick_files_multi(self, dialog: WindowInfo, folder: str, count: int) -> None:
        """
        一次选完 count 张图：
          Alt+N -> 粘贴文件夹路径 -> Enter 进入文件夹
          -> 点击第一个文件缩略图（焦点从文件名框移到列表）-> Ctrl+A -> Enter 导入

        为什么不是“粘贴多个路径”：Windows 通用对话框的“文件名”框有长度上限，
        实测 9 个带引号路径（809 字符）会被截断，只导入 2 张。
        """
        pattern = self.cfg["windows"]["file_dialog"]["title_regex"]
        attempts = int(self.timing.get("dialog_confirm_retries", 3))
        mi_cfg = self.cfg["anchor"].get("multi_image", {}) or {}
        settle = float(mi_cfg.get("enter_folder_settle_sec", 1.3))
        tile_dip = mi_cfg.get("first_tile_dip", [242.67, 163.33])
        for attempt in range(1, attempts + 1):
            self.check_timeout("file_dialog")
            self.log.info("文件对话框多选（第 %d/%d 次尝试）：一次导入 %d 张，源目录=%s",
                          attempt, attempts, count, folder)
            activate_window(dialog.hwnd, retries=3)
            time.sleep(0.6)
            pyautogui.hotkey("alt", "n")       # 聚焦“文件名”输入框
            time.sleep(0.4)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.1)
            self.paste('"' + folder + '"', "staging 文件夹路径")
            time.sleep(0.5)
            pyautogui.press("enter")           # 进入文件夹
            time.sleep(settle)

            geom = self._refresh_geometry()
            dx, dy = self._anchor_point("file_dialog", geom)
            scale = float(geom["scale"])
            tx = int(round(dx + float(tile_dip[0]) * scale))
            ty = int(round(dy + float(tile_dip[1]) * scale))
            self._click_xy(tx, ty, "文件对话框第一个文件缩略图")
            time.sleep(0.5)
            pyautogui.hotkey("ctrl", "a")      # 全选文件夹内所有图片
            time.sleep(0.6)
            if self._pre_enter_patch is None:
                self._pre_enter_patch = self._editor_probe_patch()
            t0 = time.time()
            pyautogui.press("enter")
            if self._wait_dialog_closed(pattern, timeout=5.0):
                self.log.info("已在对话框一次确认 %d 张图片（对话框关闭耗时 %.1fs）",
                              count, time.time() - t0)
                return
            self.log.warning("文件对话框仍未关闭（第 %d/%d 次多选尝试），重试", attempt, attempts)
        self.screenshot("fail_dialog_multi")
        raise StepError(EXIT_DIALOG_FAILED, "file_dialog",
                        "多图导入后文件选择对话框未关闭（文件夹路径或列表焦点异常）")

    def _grid_cell_rect(self, index: int,
                        geom: Optional[Dict] = None) -> Tuple[int, int, int, int]:
        """图片网格第 index 格（0 起，按行优先）的物理像素矩形。"""
        geom = geom or self.geom or self._refresh_geometry()
        item = self._anchor_cfg("editor_grid")
        cols = int((self.cfg["anchor"].get("editor_layout", {}) or {}).get("grid_cols", 3))
        first_x, first_y = self._anchor_dip("editor_grid", item)
        pitch = [float(v) for v in item.get("pitch", [100.0, 100.0])]
        cell = [float(v) for v in item.get("cell", [95.33, 95.33])]
        scale = float(geom["scale"])
        cx, cy, cw, ch = geom["client"]
        ox, oy = cx + cw / 2.0, cy + ch / 2.0
        row, col = divmod(index, cols)
        px = ox + (first_x + col * pitch[0]) * scale
        py = oy + (first_y + row * pitch[1]) * scale
        hw, hh = cell[0] * scale / 2.0, cell[1] * scale / 2.0
        return (int(round(px - hw)), int(round(py - hh)),
                int(round(px + hw)), int(round(py + hh)))

    def _verify_grid(self, count: int) -> Tuple[bool, str]:
        """
        逐格校验图片网格：
          前 count 格必须都有图片内容；
          第 count 格（count<9 时）必须是“+”/空白格（count=9 时不存在该格）。
        返回 (是否通过, 明细)。任何一格不符都算失败（宁可停下也不带着缺图发表）。
        """
        cfg = self._verify_cfg().get("grid", {})
        min_std = float(cfg.get("min_content_std", 12.0))
        max_mean = float(cfg.get("max_content_mean", 228.0))
        empty_mean = float(cfg.get("empty_min_mean", 232.0))
        empty_std = float(cfg.get("empty_max_std", 11.0))
        max_images = int((self.cfg["anchor"].get("editor_layout", {}) or {}).get(
            "max_images", 9))
        geom = self._refresh_geometry()
        details: List[str] = []
        ok_all = True
        for index in range(count):
            s = self._patch_stats(self._grab_patch(self._grid_cell_rect(index, geom)))
            has_image = s["std"] >= min_std or s["mean"] <= max_mean
            ok_all = ok_all and has_image
            details.append("格%d %s(std=%.1f mean=%.1f)" % (
                index + 1, "有图" if has_image else "缺图", s["std"], s["mean"]))
        if count < max_images:
            s = self._patch_stats(self._grab_patch(self._grid_cell_rect(count, geom)))
            is_empty = s["mean"] >= empty_mean and s["std"] <= empty_std
            ok_all = ok_all and is_empty
            details.append("第%d格 %s(std=%.1f mean=%.1f)" % (
                count + 1, "为“+”/空白（符合预期）" if is_empty else "意外有图", s["std"], s["mean"]))
        else:
            details.append("共 %d 张，已满格，无“+”方块（客户端上限生效）" % count)
        return ok_all, "；".join(details)

    def cancel_draft(self):
        """
        放弃当前草稿：DIP 常量定位「取消」按钮 -> 若弹出“将此次编辑保留？”再点「不保留」。

        全程只用常量换算得到的坐标，且每一步都做像素效果验证：
        点「取消」后必须能看到“面板关闭”或“确认框出现”的像素变化，
        否则立即报错退出（绝不盲点、绝不继续）。
        """
        self.log.info("清理草稿：按 DIP 常量定位编辑面板「取消」按钮")
        self._refresh_geometry()
        cx, cy = self._anchor_point("editor_cancel")
        self._debug_compare("editor_cancel", (cx, cy))
        discard_box = self._anchor_box("discard_no_keep")
        before = self._grab_patch(discard_box)
        self._click_xy(cx, cy, "编辑面板「取消」按钮")
        if self.dry_run:
            self.log.info("[DRY-RUN] 跳过草稿清理")
            return

        cfg_disc = self._verify_cfg()["discard_dialog"]
        min_diff = float(cfg_disc.get("min_diff", 0.03))
        min_dark = float(cfg_disc.get("min_dark", 0.02))
        before_dark = self._patch_stats(before)["dark"]

        def probe_cancel():
            closed, d_closed = self._panel_closed(use_feed_baseline=True)
            if closed:
                return True, f"编辑面板已关闭；{d_closed}"
            cur = self._grab_patch(discard_box)
            diff = self._diff_ratio(before, cur)
            cur_dark = self._patch_stats(cur)["dark"]
            if diff >= min_diff and cur_dark >= min_dark and cur_dark > before_dark + 0.008:
                return True, (f"出现“保留/不保留”确认框（确认框区域变化={diff:.2f}，"
                              f"dark {before_dark:.3f}->{cur_dark:.3f}）")
            return False, (f"{d_closed}；确认框区域变化={diff:.2f} "
                           f"dark={cur_dark:.3f}（点击前 {before_dark:.3f}）")

        if not self._wait_effect("点击「取消」后有反应", probe_cancel,
                                 float(cfg_disc.get("timeout_sec", 2.5)),
                                 "cancel_draft"):
            self.screenshot("fail_cancel_no_effect")
            raise StepError(EXIT_LOCATE_FAILED, "cancel_draft",
                            "点击「取消」后既没关闭编辑面板，也没出现确认框"
                            "（即“点了没反应”），已停止，未做任何后续点击")

        if self._panel_closed(use_feed_baseline=True)[0]:
            self.log.info("草稿清理完成：编辑面板已直接关闭")
            return

        dx, dy = self._anchor_point("discard_no_keep")
        self._debug_compare("discard_no_keep", (dx, dy))
        self._click_xy(dx, dy, "确认弹窗「不保留」")
        if not self._wait_effect("点击「不保留」后编辑面板关闭",
                                 lambda: self._panel_closed(use_feed_baseline=True),
                                 float(self._verify_cfg()["panel_closed"].get(
                                     "timeout_sec", 20.0)), "cancel_draft"):
            self.screenshot("fail_cancel_draft")
            raise StepError(EXIT_LOCATE_FAILED, "cancel_draft",
                            "点击「不保留」后编辑面板仍未关闭（效果验证未通过），请手动处理")
        self.log.info("草稿已放弃（「取消」->「不保留」）")

    def step_publish(self):
        self.check_timeout("publish")
        self._refresh_geometry()
        # 「发表」按钮矩形完全由 DIP 常量计算（见 _refresh_publish_rect），此处只取中心点
        x0, y0, x1, y1 = self.publish_rect
        px, py = (x0 + x1) // 2, (y0 + y1) // 2
        self._debug_compare("editor_publish", (px, py))
        self.log.info("「发表」按钮（常量）：中心=(%d,%d) 矩形=x %d~%d, y %d~%d（护栏已启用）",
                      px, py, x0, x1, y0, y1)

        if not self.cfg["publish"].get("enabled", True):
            self.log.info("publish.enabled=false，已完成定位但不点击发表；保存截图后退出")
            self.screenshot("before_publish")
            # 重要：不要把一个“已填好文字/图片”的编辑面板留在屏幕上——
            # 曾经因为事后手工点坐标清理草稿，误点进发表按钮。这里用常量 + 效果验证安全取消。
            if self.cfg["publish"].get("cancel_after_no_publish", True):
                self.cancel_draft()
            raise DryRunStop("已停在「发表」按钮前（--no-publish）；草稿已清理")

        self._click_xy(px, py, "绿色「发表」按钮", allow_publish=True)
        if self.dry_run:
            raise DryRunStop("dry-run：定位流程结束，未发表")
        time.sleep(float(self.timing["after_publish_wait_sec"]))

        if self.cfg["publish"].get("click_confirm_if_found", True):
            # 可选兜底：只在 templates 恰好存在时才尝试点二次确认；缺失就跳过（不影响流程）
            confirm = self.locate("publish_confirm", timeout=2.5,
                                  required=False, save_fail_shot=False)
            if confirm:
                self.click(confirm, "发表二次确认按钮")
                time.sleep(float(self.timing["after_publish_wait_sec"]))
            elif not self.matcher.template_variants("publish_confirm"):
                self.log.info("未提供二次确认模板；若本版本出现确认弹窗需人工确认")

        self.verify_publish()

    def verify_publish(self):
        """
        发表校验（三重判据，替代原先“按钮消失 + 相机可见”的弱判据）：
          1) 编辑面板确实关闭：`editor_publish` 与 `editor_visibility` 都不再出现；
          2) 发表后首屏 与 发表前基准首屏 的像素差比例 >= 阈值（默认 2%）；
          3) 可选 OCR：装了 pytesseract + chi_sim 时，校验刚发文字的关键片段是否出现；
             不可用时自动降级跳过（只记 DEBUG），不影响前两条判据。

        发表前基准图和发表后首屏图都写入 logs/ 并永久留存，文件名带时间戳。
        """
        fv = self._feed_verify_cfg()
        panel_timeout = float(self.cfg["publish"]["verify_editor_closed_sec"])

        # ---- 判据 1：编辑面板必须真的关闭（像素指纹变化，不依赖模板）----
        if not self._wait_effect("发表后编辑面板关闭", self._panel_closed,
                                 panel_timeout, "verify"):
            self.screenshot("fail_verify_panel")
            raise StepError(EXIT_PUBLISH_VERIFY_FAILED, "verify",
                            f"点击发表后 {panel_timeout:.0f}s 内编辑面板仍未关闭"
                            "（可见范围行与打开时的指纹仍一致），请查看失败截图")

        self.log.info("校验①通过：编辑面板已关闭")
        try:
            cam_stats = self._patch_stats(self._grab_patch(
                self._anchor_box("moments_camera")))
            self.log.info("相机位置附近像素（仅作记录）：mean=%.1f std=%.1f dark=%.3f",
                          cam_stats["mean"], cam_stats["std"], cam_stats["dark"])
        except StepError:
            pass

        if not fv.get("enabled", True):
            self.log.info("feed_verify 未启用，跳过信息流比对")
            self.screenshot("success_no_feed_verify")
            return
        if self.feed_baseline is None:
            raise StepError(EXIT_PUBLISH_VERIFY_FAILED, "verify",
                            "缺少发表前的信息流首屏基准图，无法判断信息流是否出现新内容")

        # ---- 判据 2 + 3：首屏像素差 / 可选 OCR ----
        threshold = float(fv.get("diff_threshold", 0.02))
        tolerance = int(fv.get("pixel_tolerance", 25))
        settle = float(fv.get("settle_sec", 1.2))
        feed_timeout = float(fv.get("timeout_sec", 15.0))
        feed_deadline = time.time() + feed_timeout
        last_ratio = 0.0
        last_ocr_state = "未执行"
        last_image: Optional[np.ndarray] = None

        while time.time() < feed_deadline:
            self.check_timeout("verify_feed")
            if fv.get("scroll_to_top", True):
                self._scroll_feed_to_top()
            time.sleep(settle)
            image, rect = self._grab_feed_region()
            last_image = image
            try:
                ratio = feed_diff_ratio(self.feed_baseline, image, tolerance)
            except StepError as err:
                self._save_feed_image("feed_after_publish", image)
                self.screenshot("fail_verify_feed")
                raise StepError(EXIT_PUBLISH_VERIFY_FAILED, "verify", err.message)
            last_ratio = ratio

            ocr_hit, last_ocr_state = ocr_keyword_check(
                image, self.content.text, fv.get("ocr", {}), self.log)

            diff_ok = ratio >= threshold
            self.log.info("信息流比对：变化比例=%.2f%%（阈值 %.2f%%，区域=%s）→ %s；OCR：%s",
                          ratio * 100, threshold * 100, rect,
                          "通过" if diff_ok else "未达阈值", last_ocr_state)
            if diff_ok and ocr_hit is not False:
                self.feed_after_path = self._save_feed_image("feed_after_publish", image)
                self.screenshot("success")
                self.log.info("发表成功：面板已关闭，首屏已出现新内容（变化 %.2f%%）",
                              ratio * 100)
                return
            time.sleep(0.6)

        # 超时：保留最后一次首屏图 + 失败截图，然后明确失败
        if last_image is not None:
            self.feed_after_path = self._save_feed_image("feed_after_publish_fail", last_image)
        self.screenshot("fail_verify_feed")
        raise StepError(EXIT_PUBLISH_VERIFY_FAILED, "verify",
                        f"发表后 {feed_timeout:.0f}s 内未确认信息流出现新内容"
                        f"（最后变化比例 {last_ratio * 100:.2f}%，阈值 {threshold * 100:.2f}%，"
                        f"OCR：{last_ocr_state}），请查看失败截图与首屏图")

    # ------------------------- 主流程 -------------------------
    def wait_until_scheduled(self):
        """
        定时等待。没配置 --at/--in 时立即返回。

        等待期间每 progress_interval_sec（默认 60 秒）打印一次剩余时间，
        最后 10/5/1 秒各提示一次；Ctrl+C 取消等待且不发布（退出码 9）。
        到点后不再额外倒计时，直接进入发布流程。
        """
        sched = self.cfg.get("schedule", {}) or {}
        target, desc = parse_schedule_time(
            sched.get("at", ""), sched.get("in", ""),
            float(sched.get("min_lead_sec", 10)))
        if target is None:
            return

        self.scheduled = True
        interval = max(10, int(sched.get("progress_interval_sec", 60)))
        remain = (target - datetime.now()).total_seconds()
        self.log.info("定时发表：%s → 目标 %s（剩余 %.0f 秒）。"
                      "等待期间可按 Ctrl+C 取消，取消不会发布任何内容",
                      desc, target.strftime("%Y-%m-%d %H:%M:%S"), remain)
        next_report = time.time() + interval
        milestones = {10, 5, 1}
        printed_milestones = set()
        try:
            while True:
                remaining = (target - datetime.now()).total_seconds()
                if remaining <= 0:
                    break
                now_ts = time.time()
                if now_ts >= next_report:
                    self.log.info("距定时发表还有 %.0f 秒（约 %.1f 分钟）",
                                  remaining, remaining / 60.0)
                    next_report = now_ts + interval
                for milestone in sorted(milestones, reverse=True):
                    if remaining <= milestone and milestone not in printed_milestones:
                        self.log.info("距定时发表还有 %d 秒", milestone)
                        printed_milestones.add(milestone)
                        break
                time.sleep(min(1.0, remaining))
        except KeyboardInterrupt:
            raise UserAbort("已取消定时等待，未发布任何内容")

        self.log.info("已到定时时间 %s，开始执行发布流程（不再额外倒计时）",
                      target.strftime("%Y-%m-%d %H:%M:%S"))

    def run(self) -> int:
        self._log_environment()
        self.wait_until_scheduled()
        # 定时等待可能很长，整体超时从“等待结束、真正开始操作”时重新计时
        self.total_deadline = time.time() + float(self.timing["total_timeout_sec"])
        self.step_countdown()
        self.step_find_main_window()
        self.step_open_moments()
        self.step_open_publish_editor()
        self.step_input_text()
        self.step_add_more_images()
        self.step_publish()
        self.log.info("全部流程执行完成")
        return EXIT_OK

    def _log_environment(self):
        expected = self.cfg["screen"]
        self.log.info("屏幕物理分辨率：%dx%d（配置期望 %sx%s）",
                      self.grabber.width, self.grabber.height,
                      expected.get("expected_width"), expected.get("expected_height"))
        if (self.grabber.width, self.grabber.height) != (
                expected.get("expected_width"), expected.get("expected_height")):
            self.log.warning("分辨率与测量环境（%sx%s）不一致：锚点常量可能失准（仍会按 DPI 换算尝试）",
                             expected.get("expected_width"), expected.get("expected_height"))
        self.log.info("运行方式：%s（解释器：%s）",
                      "PyInstaller 冻结 exe（随包运行时）" if getattr(sys, "frozen", False)
                      else "Python 源码模式（需目标机安装 Python 与依赖）",
                      sys.executable)
        self.log.info("dry-run=%s  发表=%s  模板阈值=%.2f（仅调试用）  缩放档=%s",
                      self.dry_run, self.cfg["publish"].get("enabled", True),
                      self.matcher.threshold, self.matcher.scales)
        self.log.info("定位方式：DIP 常量锚点；templates %s",
                      "仅用于 --debug-templates 偏差对照（不参与定位）" if self.debug_templates
                      else "不参与定位（缺失也不影响流程）")
        self.log.info("文字：%s", (self.content.text[:60] + "...")
                      if len(self.content.text) > 60 else self.content.text)
        self.log.info("图片：%s", self.content.images)

    def cleanup(self):
        self.grabber.close()


# ============================================================================
# 八、CLI
# ============================================================================
def apply_cli_overrides(cfg: Dict, args) -> Dict:
    if args.threshold is not None:
        cfg["templates"]["threshold"] = float(args.threshold)
    if args.templates_dir:
        cfg["templates"]["dir"] = args.templates_dir
    if args.countdown is not None:
        cfg["timing"]["countdown_sec"] = int(args.countdown)
    if args.total_timeout is not None:
        cfg["timing"]["total_timeout_sec"] = float(args.total_timeout)
    if args.dry_run:
        cfg["safety"]["dry_run"] = True
    if args.no_publish:
        cfg["publish"]["enabled"] = False
    if args.text is not None:
        cfg["content"]["text"] = args.text
        cfg["content"]["text_file"] = ""
    if args.text_file is not None:
        cfg["content"]["text_file"] = args.text_file
    if args.image:
        cfg["content"]["images"] = list(args.image)
    if args.log_dir:
        cfg["logging"]["dir"] = args.log_dir
    if args.at is not None:
        cfg["schedule"]["at"] = args.at
    if args.in_delay is not None:
        cfg["schedule"]["in"] = args.in_delay
    if getattr(args, "debug_templates", False):
        cfg["anchor"]["debug_templates"] = True
    return cfg


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DIP 常量定位 + 模拟键鼠，自动发送微信朋友圈（不使用微信 API / 不依赖模板）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python wechat_moments_poster.py --dry-run\n"
               "  python wechat_moments_poster.py --text \"今天心情不错\" --image D:\\pics\\1.jpg\n"
               "  python wechat_moments_poster.py --config config.json --no-publish\n"
               "  python wechat_moments_poster.py --text \"生日快乐\" --image D:\\pics\\1.jpg "
               "--at \"2026-11-07 08:00:00\"\n"
               "  python wechat_moments_poster.py --text-file content.txt --image D:\\pics\\1.jpg --in 30m\n")
    parser.add_argument("--config", default="config.json", help="JSON 配置，默认 ./config.json")
    parser.add_argument("--text", default=None, help="文字内容（覆盖配置文件）")
    parser.add_argument("--text-file", default=None, help="从 txt 读取文字")
    parser.add_argument("--image", action="append", default=None,
                        help="图片路径，可重复指定；本机微信版本必需至少一张")
    parser.add_argument("--dry-run", action="store_true", help="只定位与日志，不点击不输入")
    parser.add_argument("--no-publish", action="store_true",
                        help="走完整流程但停在“发表”按钮前，不真正发布（安全彩排）")
    parser.add_argument("--threshold", type=float, default=None, help="模板匹配阈值 0~1")
    parser.add_argument("--templates-dir", default=None, help="模板目录")
    parser.add_argument("--countdown", type=int, default=None, help="启动倒计时秒数")
    parser.add_argument("--total-timeout", type=float, default=None, help="整体超时秒数")
    parser.add_argument("--log-dir", default=None, help="日志与截图目录")
    parser.add_argument("--at", default=None,
                        help="定时发表（绝对时间），如 \"2026-11-07 08:00:00\"；与 --in 二选一")
    parser.add_argument("--in", dest="in_delay", default=None,
                        help="定时发表（相对延迟），如 20s / 30m / 2h；与 --at 二选一")
    parser.add_argument("--debug-templates", action="store_true",
                        help="仅调试：templates 存在时打印常量预测点与模板实测中心的偏差")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    # 先按 --log-dir 初始化，避免装到 Program Files 时因安装目录不可写而在启动阶段失败
    bootstrap = Logger(resolve_path(args.log_dir or DEFAULT_CONFIG["logging"]["dir"], BASE_DIR))
    poster: Optional[MomentsPoster] = None
    try:
        cfg = apply_cli_overrides(load_config(args.config, BASE_DIR), args)
        logger = Logger(resolve_path(cfg["logging"]["dir"], BASE_DIR))
        logger.info("日志文件：%s", logger.log_file)
        bootstrap = logger

        poster = MomentsPoster(cfg, logger, BASE_DIR)
        # 模板只用于 --debug-templates 的偏差对照；缺失（甚至整个目录不存在）
        # 不影响任何流程步骤，这里只提示一次。
        missing = poster.matcher.missing_required_templates()
        if missing:
            logger.info("templates 缺失 %d 个（不影响流程，定位全部改用 DIP 常量）：%s",
                        len(missing), ", ".join(missing))
        elif poster.debug_templates:
            logger.info("--debug-templates 已启用：将打印常量预测点与模板实测中心的偏差")

        return poster.run()

    except DryRunStop as stop:
        bootstrap.info("流程结束：%s", stop)
        return EXIT_OK
    except UserAbort as abort:
        bootstrap.warning("已中止：%s", abort)
        return EXIT_USER_ABORT
    except pyautogui.FailSafeException:
        bootstrap.warning("鼠标位于屏幕角落，FailSafe 触发，已中止")
        return EXIT_USER_ABORT
    except StepError as err:
        bootstrap.error("执行失败：%s", err)
        if poster:
            try:
                poster.screenshot(f"error_{err.step}")
            except Exception:
                pass
        return err.code
    except KeyboardInterrupt:
        bootstrap.warning("用户按 Ctrl+C 中止")
        return EXIT_USER_ABORT
    except Exception:
        bootstrap.error("未预期异常：\n%s", traceback.format_exc())
        if poster:
            try:
                poster.screenshot("error_unexpected")
            except Exception:
                pass
        return EXIT_UNEXPECTED
    finally:
        if poster:
            poster.cleanup()


if __name__ == "__main__":
    sys.exit(main())
