# -*- coding: utf-8 -*-
"""
微信朋友圈自动化 - 模板采集辅助工具
====================================

用途：
    在无法预先拿到微信界面截图时，用本工具把微信界面上的按钮/入口裁剪成模板图，
    保存到 templates/ 目录，供 wechat_moments_poster.py 做图像模板匹配使用。

两种用法：
1) 交互式（推荐）：
       python capture_template.py --name wc_sidebar_moments --delay 5

   - 启动后先等待 delay 秒（用于切到微信界面并打开要采集的页面）
   - 然后截取整屏，弹出窗口
   - 用鼠标拖拽框选目标区域（尽量只框图标/文字本身，四周留 4~8 像素背景）
   - 按 Enter 或点击“保存”按钮保存为 templates/<name>.png
   - 按 Esc 或点击“取消”退出

2) 按坐标直接裁剪（脚本化）：
       python capture_template.py --name wc_editor_publish --rect 1800,850,120,50 --delay 3

   坐标使用“物理像素”（DPI 感知后的屏幕坐标），与主脚本一致。

其他：
    python capture_template.py --list        # 列出 templates 目录中已有的模板

注意事项：
    - 模板必须与运行主脚本时的屏幕缩放比例/分辨率一致；换分辨率或改缩放后需重新采集。
    - 不要框入会变化的内容（头像、时间、消息文字、绿色高亮背景）。
    - 建议模板最小边不小于 16px；过小容易误匹配。
"""

import argparse
import ctypes
import os
import sys
import time

# ----------------------------------------------------------------------------
# 必须在导入 GUI / 截图库之前声明 DPI 感知，否则截到的“整屏”是缩放后的模糊图，
# 且鼠标坐标与截图像素会错位（Windows 200% 缩放下最典型）。
# ----------------------------------------------------------------------------
def enable_dpi_awareness() -> None:
    """声明进程为 Per-Monitor DPI Aware，保证截图与鼠标坐标都基于物理像素。"""
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


enable_dpi_awareness()

import mss  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageTk  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")


def grab_fullscreen() -> np.ndarray:
    """截取主显示器整屏，返回 PIL.Image (RGB)。"""
    # mss 10.x 用 MSS()，旧版本用 mss()，这里做兼容
    factory = getattr(mss, "MSS", None) or mss.mss
    with factory() as sct:
        monitor = sct.monitors[1]  # 1 号监视器 = 主屏，0 号是所有屏幕的联合区域
        raw = np.array(sct.grab(monitor))  # BGRA
    # BGRA -> RGB
    rgb = raw[:, :, [2, 1, 0]]
    return Image.fromarray(rgb, mode="RGB")


def parse_rect(text: str) -> tuple:
    """解析 'x,y,w,h' 形式的坐标字符串。"""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--rect 需要 4 个数字：x,y,w,h")
    try:
        x, y, w, h = (int(float(p)) for p in parts)
    except ValueError:
        raise argparse.ArgumentTypeError("--rect 的 4 个值必须是数字")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("--rect 的宽高必须大于 0")
    return x, y, w, h


def save_crop(image: Image.Image, rect: tuple, name: str, template_dir: str) -> str:
    """从整屏图中裁剪矩形并保存为模板。"""
    x, y, w, h = rect
    x = max(0, x)
    y = max(0, y)
    w = min(w, image.width - x)
    h = min(h, image.height - y)
    if w <= 0 or h <= 0:
        raise ValueError(f"裁剪区域超出屏幕范围：{rect}")

    crop = image.crop((x, y, x + w, y + h))
    os.makedirs(template_dir, exist_ok=True)
    filename = name if name.lower().endswith(".png") else name + ".png"
    path = os.path.join(template_dir, filename)
    crop.save(path, "PNG")
    print(f"[OK] 已保存模板：{path}  尺寸={crop.width}x{crop.height}  坐标={x},{y},{w},{h}")
    if min(crop.width, crop.height) < 16:
        print("[警告] 模板最短边小于 16px，容易误匹配，建议重新框选稍大一点。")
    return path


class CaptureApp:
    """交互式框选窗口：把整屏缩放到窗口内显示，拖拽后映射回原图坐标。"""

    MAX_W = 1280  # 显示窗口最大宽度（逻辑像素）
    MAX_H = 800

    def __init__(self, image: Image.Image, default_name: str, template_dir: str):
        import tkinter as tk
        from tkinter import messagebox

        self.tk = tk
        self.messagebox = messagebox
        self.image = image
        self.template_dir = template_dir
        self.default_name = default_name
        self.start = None
        self.rect_id = None
        self.selection = None

        # 计算显示缩放比例：整屏可能 1920 宽，缩小到窗口可容纳
        self.display_scale = min(1.0, self.MAX_W / image.width, self.MAX_H / image.height)
        disp_w = max(1, int(image.width * self.display_scale))
        disp_h = max(1, int(image.height * self.display_scale))
        self.disp_size = (disp_w, disp_h)

        self.root = tk.Tk()
        self.root.title("模板采集：拖拽框选目标区域（Enter 保存 / Esc 取消）")
        self.canvas = tk.Canvas(self.root, width=disp_w, height=disp_h, cursor="crosshair",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        resized = image.resize((disp_w, disp_h), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(resized)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

        self.info = tk.Label(self.root, anchor="w", justify="left",
                             text=("拖拽框选要作为模板的区域；尽量只包含目标图标/文字，"
                                   "不要包含时间、头像等会变化的内容。"))
        self.info.pack(fill="x", padx=6, pady=4)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Return>", lambda e: self.on_save())
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def _canvas_to_image(self, cx: int, cy: int) -> tuple:
        """画布坐标 -> 原图物理像素坐标。以画布实际尺寸为准，避免窗口被 DPI 缩放影响。"""
        cw = self.canvas.winfo_width() or self.disp_size[0]
        ch = self.canvas.winfo_height() or self.disp_size[1]
        ix = cx * self.image.width / cw
        iy = cy * self.image.height / ch
        return int(round(ix)), int(round(iy))

    def on_press(self, event):
        self.start = (event.x, event.y)
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                    outline="#ff3b30", width=2)

    def on_drag(self, event):
        if self.rect_id and self.start:
            self.canvas.coords(self.rect_id, self.start[0], self.start[1], event.x, event.y)
            x0, y0 = self._canvas_to_image(*self.start)
            x1, y1 = self._canvas_to_image(event.x, event.y)
            self.info.config(text=f"当前选区（物理像素）：x={min(x0, x1)}, y={min(y0, y1)}, "
                                  f"w={abs(x1 - x0)}, h={abs(y1 - y0)}")

    def on_release(self, event):
        if not self.start:
            return
        x0, y0 = self._canvas_to_image(*self.start)
        x1, y1 = self._canvas_to_image(event.x, event.y)
        left, top = min(x0, x1), min(y0, y1)
        width, height = abs(x1 - x0), abs(y1 - y0)
        if width < 4 or height < 4:
            print("[提示] 选区太小，请重新拖拽。")
            return
        self.selection = (left, top, width, height)
        print(f"[选区] x={left}, y={top}, w={width}, h={height}")

    def on_save(self):
        if not self.selection:
            self.messagebox.showwarning("提示", "请先用鼠标拖拽框选区域。")
            return
        name = self.default_name
        if not name:
            from tkinter import simpledialog
            name = simpledialog.askstring("模板名称", "请输入模板文件名（不含 .png）：",
                                          initialvalue="wc_template")
            if not name:
                return
        try:
            save_crop(self.image, self.selection, name, self.template_dir)
        except Exception as exc:
            self.messagebox.showerror("保存失败", str(exc))
            return
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="微信朋友圈自动化-模板采集工具")
    parser.add_argument("--name", default="", help="模板文件名（可省略 .png）")
    parser.add_argument("--rect", type=parse_rect, default=None,
                        help="非交互模式：直接按物理像素坐标裁剪，格式 x,y,w,h")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="截图前等待秒数，默认 3 秒（用来到微信界面摆好状态）")
    parser.add_argument("--templates-dir", default=DEFAULT_TEMPLATE_DIR,
                        help="模板目录，默认 ./templates")
    parser.add_argument("--list", action="store_true", help="列出已有模板后退出")
    args = parser.parse_args()

    if args.list:
        if not os.path.isdir(args.templates_dir):
            print(f"模板目录不存在：{args.templates_dir}")
            return
        files = sorted(f for f in os.listdir(args.templates_dir)
                       if f.lower().endswith(".png"))
        print(f"模板目录：{args.templates_dir}  共 {len(files)} 个")
        for f in files:
            print("  -", f)
        return

    if args.delay > 0:
        print(f"[等待] {args.delay} 秒后截图，请现在切换到需要采集的微信界面……")
        for left in range(int(args.delay), 0, -1):
            print(f"\r倒计时 {left} 秒 ", end="", flush=True)
            time.sleep(1)
        print("\r开始截图                ")

    image = grab_fullscreen()
    print(f"[截图] 物理分辨率 {image.width}x{image.height}")

    if args.rect:
        if not args.name:
            print("[错误] 使用 --rect 时必须同时指定 --name")
            sys.exit(2)
        save_crop(image, args.rect, args.name, args.templates_dir)
        return

    CaptureApp(image, args.name, args.templates_dir).run()


if __name__ == "__main__":
    main()
