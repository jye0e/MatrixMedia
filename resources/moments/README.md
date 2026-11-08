# 微信朋友圈自动发布脚本（Windows / DIP 常量定位 + 模拟键鼠）

用**屏幕截图（mss）+ DIP 常量定位 + pyautogui 模拟键鼠 + pywin32 窗口管理**在 Windows 微信 PC 版
发布朋友圈。模板匹配已降级为调试手段（`--debug-templates`），运行时不依赖 `templates/`。
不使用微信 API、不做协议逆向、不注入 Hook、不使用 uiautomation 控件树。

## 0. 随包运行时：目标机不需要安装 Python

MatrixMedia 安装包自带 **PyInstaller 冻结运行时**：

```
resources/moments-runtime/wechat_moments_poster.exe   ← 双击即可运行（自带 Python 3 + 全部依赖）
resources/moments-runtime/_internal/                  ← 依赖（numpy / Pillow / mss / pyautogui / pywin32）
resources/moments-runtime/config.json                 ← 配置
resources/moments-runtime/templates/                  ← 模板（仅 --debug-templates 用）
```

- 客户端（`src/main/services/upLoad/pyq.js`）打包态**优先启动这个 exe**，不查 PATH、不调 `py`；
  运行时缺失才回退到源码模式（需要目标机 Python，仅开发态使用）。
- 安装包构建时由 `scripts/build-moments-runtime.js` 自动生成（`npm run build:moments-runtime`），
  该目录是**构建产物、不入库**（见 `.gitignore`）。
- 体积：约 64 MB（已排除 OpenCV 与 tkinter）；启动实测 --help 2.1~2.8 秒
  （同机源码模式 3.8~4.6 秒；onedir 无解包开销，首次运行可能因杀毒扫描更慢）。
- 不含 OpenCV：灰度差/拉普拉斯方差/PNG 编码全部改为纯 numpy + Pillow，实测与 cv2 差异可忽略
  （灰度 max|diff|=1、Laplacian var 1004.3 vs 1007.2）。
- 不含 OpenCV 的后果：`--debug-templates` 模板对照不可用；**定位与发表流程完全不受影响**。
- 不含 pytesseract：OCR 判据自动降级跳过（见 7.2），不影响发表。

## 0.1 快速开始（源码模式，需要本机 Python）

```powershell
cd <文件夹路径>
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 1) 查看自带模板；若分辨率/缩放/微信主题不同，用采集工具重采
.\.venv\Scripts\python.exe capture_template.py --list

# 2) 只定位、不点击：验证路径、模板和窗口标题
.\.venv\Scripts\python.exe wechat_moments_poster.py --dry-run --text "测试文字" --image D:\pics\a.jpg

# 3) 彩排：真实打开朋友圈、选图、输入，但停在“发表”前并自动清理草稿
.\.venv\Scripts\python.exe wechat_moments_poster.py --no-publish --text "彩排，请忽略" --image D:\pics\a.jpg

# 4) 正式发布；或定时发布（--at 绝对时间 / --in 相对延迟）
.\.venv\Scripts\python.exe wechat_moments_poster.py --text "内容" --image D:\pics\a.jpg
.\.venv\Scripts\python.exe wechat_moments_poster.py --text "内容" --image D:\pics\a.jpg --at "2026-11-07 08:00:00"
```

提示：所有路径都相对脚本所在目录解析，把整个文件夹放到任意位置都能直接运行；
定位靠 `config.json` 的 DIP 常量（`anchor.items`），`templates/` 只在 `--debug-templates` 时用到；
若换了分辨率/缩放/微信版本导致常量失配，需要用 probe 重新测量并更新常量（见 4.1）。

## 1. 文件清单

| 文件 | 说明 |
|---|---|
| `wechat_moments_poster.py` | 主脚本（定位 → 点击 → 输入 → 选图 → 发表） |
| `capture_template.py` | 模板采集工具（整屏截图 + 拖拽框选/坐标裁剪，保存到 `templates/`） |
| `config.json` | 配置：模板目录、阈值、等待时间、文字内容、图片路径、安全开关 |
| `templates/` | 模板图目录（默认已含本机实测采集的 11 张） |
| `requirements.txt` | 依赖列表 |
| `examples/content_example.txt` | 示例文案（分享目录内附，可直接配合 `--text-file` 使用） |
| `logs/` | 运行日志与失败/成功截图（运行时自动生成） |

## 2. 环境与实测情况（本机）

- 系统：Windows，物理分辨率 **1920×1080**，系统缩放 **150%**（逻辑坐标 1280×720）
- 微信：`Weixin` PC 版 **4.2.0.13**（主窗口标题“微信”，位置随窗口状态变化）
- 脚本启动时声明 Per-Monitor DPI 感知，因此截图与鼠标坐标都是**物理像素**。

**本机已实测（真实点击，未真正发布）：**
1. 找到并激活“微信”主窗口；
2. 点左侧“朋友圈”图标 → 弹出“发现”浮层 → 点浮层“朋友圈”行 → 朋友圈页面打开
   （实测该版本页面可能直接显示在主窗口内，**不一定**新开标题为“朋友圈”的独立窗口，
   所以脚本用“相机图标出现”判断页面就绪，不依赖窗口标题）；
3. 点右上角相机 → 弹出“选择文件”对话框（**该版本必须先选图片才会出现编辑面板**）；
4. 对话框文件名框粘贴路径 → 回车 → 编辑面板出现（含缩略图、“+”方块、`发表/取消`）；
5. 点“这一刻的想法...”输入中文 → 成功；
6. 定位到绿色“发表”按钮（得分 1.000）后停止（`--no-publish`），再由脚本用模板清理草稿。

**2026-11-06 20:02 彩排（`--no-publish`，含图片 + 中文）：** 脚本自身在“发表”按钮处（1.000）
按设计停止、未点击发表；但**彩排后由人工清理草稿时发生了误发表**——当时按截图目测坐标点“取消”，
而该次布局的绿色“发表”按钮像素范围为 x 491~655，点击坐标 656 落在按钮内 1 像素，
20:03:01 内容被真实发出（微信本地 `db_storage\sns\sns.db` 与 `cache\...\Sns\Img` 在
20:03:01~20:03:02 被写入为佐证），随后由用户本人删除。**此前“未发布任何内容”的结论是错的。**

**修正后复验（20:15，同一环境）：** 相机 1.000 → 对话框选图 → 编辑面板 1.000 → 输入区 1.000
→ 文字粘贴 → 发表按钮 1.000（像素矩形 x 491~655, y 733~770）→ 脚本自动用模板点击
「取消」1.000 (749,750) → 「不保留」1.000 (749,499) → 编辑面板关闭，退出码 0，
信息流无新增内容。

**2026-11-06 20:26 正式发布验证（`publish.enabled=true`，脚本自主点击，无人工干预）：**
窗口先移动到 (100,60)（模板采集时为 (-7,17)，尺寸同为 1342×901），相机 1.000 → 选图对话框
→ 编辑面板 1.000 → 输入区 1.000 → 发表按钮 1.000 (679,795) → 点击发表 → 4.1 秒后
`verify_publish` 判定：发表按钮消失（最高分 0.384 < 阈值）、相机图标恢复 1.000，
退出码 0。信息流顶部出现该测试帖，未删除，保留待用户核对后自行删除；
截图 `logs/success_20261106_202648.png`。

**未实测的部分（如实说明）：**
- 其他微信版本、其他分辨率/缩放、纯文字发表（该版本未找到纯文字入口，点相机必须先选图）。
- 发表后的“二次确认弹窗”模板 `wc_publish_confirm.png` 未采集（标记为可选，未找到时跳过）。

## 3. 安装

```powershell
cd C:\Users\Administrator\Documents\Trae-CN\temp_20261106_01
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

本机已安装好虚拟环境 `.venv`，可直接使用 `.\.venv\Scripts\python.exe` 运行。

## 4. 模板清单（仅 `--debug-templates` 使用）

> **运行时必需性：否。** 所有定位已改为 `config.json` 的 DIP 常量（`anchor.items`），
> 模板缺失不影响 dry-run / 彩排 / 正式发表任何一步；下表“必需”指**模板调试对照**时是否必需。
> 随包运行时已排除 OpenCV，`--debug-templates` 在该运行时下不可用（会提示 cv2 缺失）。

模板必须与运行时的**分辨率 + 缩放比例 + 微信主题**一致。换机器/换缩放/换版本需重新采集。

| 模板文件（`templates/` 下） | 用途 | 必需 |
|---|---|---|
| `wc_sidebar_moments.png` | 主窗口左侧导航栏“朋友圈”图标（灰色未选中态） | 是 |
| `wc_sidebar_moments_active.png` | 同上图标（绿色对勾，已进入朋友圈时的外观），作为备用外观 | 否（`alt_files`） |
| `wc_discover_moments.png` | “发现”浮层里的“朋友圈”一行（灰色未选中） | 是 |
| `wc_discover_moments_active.png` | 同上，绿色选中态外观，作为备用外观 | 否（`alt_files`） |
| `wc_moments_camera.png` | 朋友圈右上角相机（发表）图标 | 是 |
| `wc_editor_input.png` | 编辑面板“这一刻的想法...”占位文字 | 是（缺失时会退化为“直接粘贴”） |
| `wc_editor_add_image.png` | 编辑面板“+”添加图片方块（用于添加多图） | 是 |
| `wc_editor_visibility.png` | 编辑面板“谁可以看 … 公开”整行（**编辑面板就绪判断**） | 是 |
| `wc_editor_publish.png` | 编辑面板绿色“发表”按钮（**必须裁紧**，否则易误匹配到其他绿色按钮） | 是 |
| `wc_editor_cancel.png` | 编辑面板灰色“取消”按钮（--no-publish 结束后自动清理草稿） | 是 |
| `wc_discard_nokeep.png` | “将此次编辑保留？”弹窗中的“不保留”按钮 | 是 |
| `wc_publish_confirm.png` | （可选）发表后的二次确认按钮 | 否 |

采集要点：
- 只框图标/文字本身，四周留 4~8 像素背景；**不要**框入头像、时间、昵称等会变化的内容。
- 模板最短边建议 ≥ 16px，太小容易误匹配。
- 尤其是 `wc_editor_publish.png`：只裁绿色按钮本身（本机为 165×38），不要带大片白底，
  否则 `TM_CCOEFF_NORMED` 可能在其他浅色背景上得到 0.9 的假阳性（实测踩过此坑）。
- `wc_editor_visibility.png` 用来判断“编辑面板已出现”，不要用“发表”按钮或“+”方块判断：
  文件对话框里若正好浏览到这些模板图（缩略图），会得到 0.9+ 的假阳性（实测踩过）。

### 4.1 采集方法

```powershell
# 交互式：等待 N 秒（用于把微信界面摆到目标状态），然后截整屏，窗口里拖拽框选，Enter 保存
.\.venv\Scripts\python.exe capture_template.py --name wc_editor_publish --delay 5

# 非交互：已知物理像素坐标时直接裁剪（x,y,w,h）
.\.venv\Scripts\python.exe capture_template.py --name wc_moments_camera --rect 1188,86,38,28 --delay 3

# 查看已有模板
.\.venv\Scripts\python.exe capture_template.py --list
```

交互窗口里：鼠标拖拽框选 → 按 `Enter` 或点“保存” → 写入 `templates/<name>.png`；
`Esc` 取消。若 `--name` 未指定，保存时会弹框让你输入名称。

## 5. 配置（`config.json`）

```jsonc
{
  "windows": {
    "main": { "title_regex": "^(微信|WeChat)$" },       // 主窗口标题正则
    "file_dialog": { "title_regex": "(打开|Open|选择文件)" } // 选图对话框标题正则
  },
  "templates": {
    "dir": "templates",
    "threshold": 0.80,          // 全局匹配阈值（items 里可单独覆盖）
    "scales": [1.0, 0.9, 0.8, 1.1, 1.25, 1.5],   // 多尺度匹配
    "items": { "…": { "file": "...", "alt_files": ["..."], "roi": [...], "threshold": 0.85 } }
  },
  "timing": {
    "countdown_sec": 5,              // 启动倒计时
    "step_wait_sec": 0.8,            // 每步之间的等待
    "locate_timeout_sec": 8.0,       // 单次定位超时
    "locate_retry_interval_sec": 0.4,
    "window_open_timeout_sec": 20.0, // 等朋友圈页面加载出相机图标的超时
    "moments_open_retries": 2,       // 页面没加载出来时重复点击“朋友圈”次数
    "dialog_timeout_sec": 15.0,      // 等文件对话框出现
    "dialog_confirm_retries": 3,     // 粘贴路径+回车的重试次数
    "editor_open_timeout_sec": 20.0,
    "total_timeout_sec": 240,        // 整体超时
    "after_publish_wait_sec": 1.5
  },
  "content": { "text": "", "text_file": "", "images": [] },
  "publish": {
    "enabled": true,
    "click_confirm_if_found": true,
    "cancel_after_no_publish": true,
    "verify_editor_closed_sec": 20.0,   // 等编辑面板关闭的超时
    "feed_verify": {                    // 发表后“信息流里真的出现新内容”校验
      "enabled": true,
      "roi": [0.30, 0.05, 1.0, 1.0],    // 首屏区域，相对微信主窗口
      "diff_threshold": 0.02,           // 像素差比例阈值（2%）
      "pixel_tolerance": 25,            // 单像素灰度差超过它才算“变化”
      "scroll_to_top": true,            // 对比前后都先滚到顶部
      "scroll_units": 30,               // 每次向上滚动格数
      "settle_sec": 1.2,                // 滚动后等待渲染稳定
      "timeout_sec": 15.0,              // 面板关闭后等新内容的超时
      "ocr": {                          // 可选 OCR（未安装时自动降级跳过）
        "enabled": true,
        "lang": "chi_sim",
        "min_chars": 6                  // 取正文前 N 个字符作为关键片段
      }
    }
  },
  "safety": { "dry_run": false },
  "schedule": {
    "at": "",                         // 绝对时间 "YYYY-MM-DD HH:MM:SS"；留空表示不定时
    "in": "",                         // 相对延迟 "20s" / "30m" / "2h"；与 at 二选一
    "progress_interval_sec": 60,      // 等待期间打印剩余时间的间隔（默认每分钟）
    "min_lead_sec": 10                // 距目标不足该秒数直接报错（防止误发）
  },
  "screen": { "expected_width": 1920, "expected_height": 1080, "expected_dpi_scale": 1.5 },
  "logging": { "dir": "logs" }
}
```

- `roi`：`null` = 全屏搜索；`[x0,y0,x1,y1]`（0~1 的小数）= 相对微信主窗口的搜索区域。
  `sidebar_moments` / `discover_moments` 用 ROI 缩小范围，避免误匹配到桌面其他窗口。
- 分辨率/缩放与 `screen.expected_*` 不一致时脚本会**告警**（不中断），提示需要重新采集模板。

## 6. 运行

```powershell
# 最安全：dry-run，只定位与打日志，不点击不输入
.\.venv\Scripts\python.exe wechat_moments_poster.py --dry-run --text "测试文字" --image D:\pics\a.jpg

# 彩排：走完整真实流程，但停在“发表”按钮前，不真正发布（推荐先跑这个）
.\.venv\Scripts\python.exe wechat_moments_poster.py --no-publish `
    --text "自动化测试内容，请忽略" --image D:\pics\a.jpg

# 正式发布：文字来自 txt，图片可重复给多张
.\.venv\Scripts\python.exe wechat_moments_poster.py `
    --text-file content.txt --image D:\pics\a.jpg --image D:\pics\b.jpg --countdown 5
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--config` | 配置文件路径，默认 `config.json` |
| `--text` / `--text-file` | 文字内容 / 从 txt（UTF-8）读取 |
| `--image`（可重复） | 图片绝对路径；第一张在“选择文件”对话框中选入 |
| `--dry-run` | 只截图定位 + 日志，不点击不输入 |
| `--no-publish` | 真实走流程但不点“发表”（安全彩排） |
| `--threshold` | 覆盖全局模板阈值 |
| `--templates-dir` | 覆盖模板目录 |
| `--countdown` | 启动倒计时秒数（定时模式到点后自动跳过） |
| `--total-timeout` | 整体超时秒数（定时等待结束后才开始计时） |
| `--log-dir` | 日志与截图目录 |
| `--at` | 定时发表（绝对时间），如 `"2026-11-07 08:00:00"`；与 `--in` 二选一 |
| `--in` | 定时发表（相对延迟），如 `20s`、`30m`、`2h`；与 `--at` 二选一 |

### dry-run 与 `--no-publish` 的区别

- `--dry-run`：**完全不操作**微信，只在截图上做模板定位。因为不点击，很多后续界面不会出现，
  所以通常只验证到“左侧朋友圈图标”或“相机图标”就会结束，并打印“本应点击 (x,y)”。
- `--no-publish`：**真的**点开朋友圈、选图、输入文字，最后停在“发表”按钮前，
  保存 `logs/before_publish_*.png`，然后**自动**用模板点「取消」→「不保留」清理草稿
  （由 `publish.cancel_after_no_publish` 控制，默认开启），不会把编辑面板留在屏幕上。

### 6.1 定时发表（`--at` / `--in`）

```powershell
# 绝对时间：2026-11-07 08:00:00 自动发布
.\.venv\Scripts\python.exe wechat_moments_poster.py `
    --text-file content.txt --image D:\pics\a.jpg --at "2026-11-07 08:00:00"

# 相对延迟：30 分钟后发布
.\.venv\Scripts\python.exe wechat_moments_poster.py `
    --text "内容" --image D:\pics\a.jpg --in 30m
```

| 参数 | 示例 | 说明 |
|---|---|---|
| `--at` | `"2026-11-07 08:00:00"` / `"2026-11-07 08:00"` / `...T08:00:00` | 本地时区绝对时间 |
| `--in` | `20s` / `30m` / `2h` | 从现在起的相对延迟，单位必须是 s/m/h |

行为约定：

- 等待期间**不操作微信**；每 `progress_interval_sec`（默认 60 秒）打印一次剩余时间，
  最后 10/5/1 秒各提示一次，不会刷屏。
- 等待中按 **Ctrl+C** 取消：退出码 **9**，不会发布任何内容。
- 到点后**跳过启动 5 秒倒计时**，直接走完整发布流程；此时若微信未运行、被最小化或被遮挡，
  沿用原有逻辑（自动恢复/前置，失败则退出码 4/5 并保存截图，不盲目点击）。
- 整体超时（默认 240 秒）从**等待结束、真正开始操作**时重新计时，长时间等待不会误触发超时。
- 以下情况一律在开始前报错（退出码 **2**，不发布）：`--at`/`--in` 同时给、格式错误、
  时间已经过去、距目标不足 `min_lead_sec`（默认 10 秒）。

### 6.2 用 Windows 任务计划定时（关机/睡眠场景）

内置等待（`--at`/`--in`）要求：**机器保持开机、进程存活、用户已登录且桌面可截图**。

如果希望“到点自动拉起”，可以用 `schtasks`：

```powershell
$dir = "C:\path\to\wechat_moments_poster_share"
$py  = "$dir\.venv\Scripts\python.exe"
$cmd = "`"$py`" `"$dir\wechat_moments_poster.py`" --text-file `"$dir\content.txt`" " +
       "--image `"D:\pics\a.jpg`" --countdown 5"

schtasks /Create /TN "WechatMomentsPoster" /TR $cmd /SC ONCE /ST 08:00 /SD 2026/11/07 /RL LIMITED /F
schtasks /Query /TN "WechatMomentsPoster" /V /FO LIST
schtasks /Delete /TN "WechatMomentsPoster" /F
```

区别与注意：

- 任务计划方式机器**关机时仍然不会执行**；睡眠则取决于是否启用“唤醒计算机运行此任务”，
  需要到“任务计划程序 → 属性 → 条件”里勾选唤醒，并确认电源计划允许唤醒。
- 任务必须运行在**用户已登录的交互式会话**中（不能用“不管用户是否登录都运行”），
  否则没有桌面，截图和键鼠都会失败。建议保持屏幕解锁，锁屏可能导致截图黑屏。
- 任务计划默认工作目录是 `system32`，但脚本的配置/模板/日志都相对**脚本所在目录**解析，
  不依赖工作目录；仍建议在 `/TR` 里写全绝对路径。
- 任务时间到点后仍会先走脚本的 5 秒启动倒计时（可用 `--countdown 0` 关闭）。

## 7. 安全与稳健性

- **点击护栏**：定位到「发表」按钮后会记录其物理像素矩形（本机为 x 491~655, y 733~770）；
  之后除“发表”步骤本身，任何点击只要落进这个矩形就拒绝执行并以退出码 10 中止。
  这是 2026-11-06 误发表事故的直接防线。
- **清理草稿只用模板**：`cancel_draft()` 通过 `wc_editor_cancel.png` / `wc_discard_nokeep.png`
  定位中心点点击，禁止任何硬编码或截图目测坐标。
- 启动倒计时（默认 5 秒），期间把鼠标移到屏幕任意角落即可中止；
- `pyautogui.FAILSAFE` 常开：运行中鼠标移到屏幕角落立即抛异常并退出（退出码 9）；
- 任何一步定位失败：**不盲点**，立即 `ERROR` 日志 + 保存 `logs/fail_*.png` + 明确退出码退出；
- 每步可配置等待/重试；整体超时保护（默认 240 秒，退出码 8）；
- 选图对话框：`Alt+N` 聚焦文件名框 → `Ctrl+A` 清空 → `Ctrl+V` 粘贴路径 → `Enter`，
  失败自动整段重试（默认 3 次），全程不读取控件树。

退出码：`0` 成功 / `2` 配置错误 / `3` 模板缺失 / `4` 找不到微信窗口 / `5` 定位失败 /
`6` 文件对话框失败 / `7` 发表校验失败 / `8` 整体超时 / `9` 用户中止 /
`10` 点击护栏拦截（目标落在“发表”按钮内） / `99` 未预期异常。

### 7.1 事故复盘：2026-11-06 20:03 误发表

- **现象**：`--no-publish` 彩排后，一条“测试彩排，请忽略”的朋友圈被真实发出（用户已删除）。
- **原因**：不是脚本点的。彩排结束后人工清理草稿时，坐标是按 1024×576 缩放截图**目测**得出的
  (656,748)，换算成物理像素后正好落在绿色「发表」按钮内 1 像素（该按钮实际范围 x 491~655）。
  点“发表”会立刻关闭编辑面板且**不弹**“保留/不保留”确认框，所以当时看起来像“取消成功”。
- **证据**：脚本日志三次运行均停在 `publish.enabled=false` 分支、无发表点击记录；
  绿色按钮像素边界实测为 x 490~657；误点后无确认弹窗；微信 `sns.db` 与 `Sns/Img`
  缓存在 20:03:01~20:03:02 写入。
- **修正**：① 新增「取消」「不保留」模板与 `cancel_draft()`；② `--no-publish` 结束后自动清理草稿；
  ③ 增加点击护栏（落进发表矩形即中止，退出码 10）；④ 文档明确禁止用截图目测坐标点击。

### 7.2 发表后校验：三重判据（2026-11-06 新增）

发表成功后不再只看“按钮消失 + 相机可见”，而是：

1. **面板确实关闭**：`editor_publish` 与 `editor_visibility` 两个模板都持续匹配不到；
2. **信息流首屏像素差**：点相机之前先留存 `logs/baseline_feed_<时间戳>.png`，
   发表后滚到顶部再截一张 `logs/feed_after_publish_<时间戳>.png`，
   两张图在 `publish.feed_verify.roi` 区域内做灰度差比对，
   变化像素占比 ≥ `diff_threshold`（默认 2%）才算“信息流出现了新内容”；
3. **可选 OCR**：若装好 `pytesseract` + tesseract + `chi_sim` 语言包，
   对首屏做 OCR，检查正文前 `min_chars` 个字符（默认 6）是否出现。
   缺依赖/缺语言包/OCR 异常时**自动降级跳过**，只写 DEBUG 日志，不会让校验失败。

任一硬判据不满足会继续等待，超过 `feed_verify.timeout_sec`（默认 15 秒）后以
**退出码 7** 失败，同时保留 `feed_after_publish_fail_*.png` 与 `fail_verify_feed_*.png`。
基准图与发表后首屏图**永久保留**，脚本不会删除或覆盖它们。

**OCR 安装（可选）：**

```powershell
# 1) Python 包
.\.venv\Scripts\python.exe -m pip install pytesseract

# 2) Tesseract 引擎（Windows 安装包或 winget）
winget install UB-Mannheim.TesseractOCR
#    安装后把安装目录加入 PATH，或设置环境变量：
#    setx TESSDATA_PREFIX "C:\Program Files\Tesseract-OCR\tessdata"

# 3) 中文语言包：下载 chi_sim.traineddata 放进 tessdata 目录
#    https://github.com/tesseract-ocr/tessdata/raw/main/chi_sim.traineddata

# 4) 自检
.\.venv\Scripts\python.exe -c "import pytesseract; print(pytesseract.get_languages(config=''))"
```

若第 4 步输出里没有 `chi_sim`，脚本日志会在 DEBUG 级别写“缺少语言包 chi_sim，OCR 判据跳过”。

## 8. 需要你校准的地方

1. **模板**：本机 11 张模板取自“微信 4.2.0.13 / 1920×1080 / 150% / 浅色主题”。
   若你的分辨率、缩放、微信版本或主题不同，请用 `capture_template.py` 全部重采。
2. **窗口标题**：若主窗口标题不是“微信”（例如英文版 `WeChat`），改 `windows.main.title_regex`。
3. **该版本必须选图**：微信 4.2.0.13 点相机先弹“选择文件”，因此 `content.images` 至少给 1 张；
   若你的版本能直接进编辑面板，脚本会自动识别（同时轮询编辑面板与文件对话框）。
4. **发表校验**：旧版“按钮消失 + 相机可见”判据已在 4.2.0.13 真机发布验证（20:26，判定正确）；
   新的三重判据已用保留截图做正/负样本离线验证，尚未再跑一次真实发布。首次正式发布仍建议
   先 `--no-publish` 彩排，并人工确认信息流里确实出现新内容。
5. **首次运行前**：请确认朋友圈页面已能正常加载（网络慢时页面可能空白，脚本会等待
   `window_open_timeout_sec` 并重试点击，最多 `moments_open_retries` 次）。
