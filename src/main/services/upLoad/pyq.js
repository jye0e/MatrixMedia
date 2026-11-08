"use strict";

/**
 * 朋友圈（微信朋友圈）平台适配层。
 *
 * 朋友圈没有可用的 Web 后台，因此**不复用**本项目基于 Puppeteer 的浏览器自动化，
 * 而是转调本机已有的桌面自动化脚本 `wechat_moments_poster.py`
 * （mss 截图 + OpenCV 模板匹配 + pyautogui 模拟键鼠 + pywin32 窗口管理）。
 *
 * 已知约束（如实保留，不做伪造）：
 *   - 仅支持 Windows 桌面交互会话 + 微信 PC 版；Electron 开发/安装版均可，但容器或
 *     无桌面会话的服务器上无法运行（脚本找不到微信窗口，会以退出码 4 返回）。
 *   - 该版本微信点击相机后必须先选图，**纯文字发不了**，所以至少需要一张图片
 *     或一段视频（图片与视频不能混选）。
 *   - 视频同样通过脚本的 `--image` 入口选中（微信文件对话框选视频后进入视频编辑
 *     面板）；视频发表后微信需要转码，脚本的等待/校验窗口由这里生成的临时
 *     config 调大。
 *   - 换分辨率 / 系统缩放 / 微信主题 / 微信版本后需用模板采集工具重采模板。
 *   - 模板匹配失败会返回退出码 3 / 5，需要人工按日志排查。
 *
 * 运行方式：
 *   打包版优先使用随包附带的冻结运行时 `resources/moments-runtime/wechat_moments_poster.exe`
 *   （由 scripts/build-moments-runtime.js 生成），目标机**不需要**安装 Python 与依赖；
 *   运行时缺失时才回退到「本机 Python + resources/moments/*.py」的源码模式（开发态/兜底）。
 *
 * 可通过环境变量覆盖路径：
 *   MATRIXMEDIA_MOMENTS_RUNTIME 自带运行时 exe 路径（默认打包版 resources/moments-runtime/*.exe）
 *   MATRIXMEDIA_MOMENTS_SCRIPT  源码模式脚本绝对路径（默认打包版 resources/moments，开发态本机脚本）
 *   MATRIXMEDIA_MOMENTS_PYTHON  Python 解释器（源码模式默认 `py`，即 Windows Python Launcher）
 *   MATRIXMEDIA_MOMENTS_TEMPLATES_DIR  模板目录（默认脚本同目录 templates/）
 *   MATRIXMEDIA_MOMENTS_LOG_DIR 日志与截图目录（默认「文档\MatrixMedia\data\moments-logs」）
 *
 * 退出码契约（与脚本 README 一致）：
 *   0 成功 / 2 配置错误 / 3 模板缺失 / 4 找不到微信窗口 / 5 定位失败 /
 *   6 文件对话框失败 / 7 发表校验失败 / 8 超时 / 9 用户中止 / 10 点击护栏拦截 /
 *   99 未预期异常
 */

import fs from "fs";
import os from "os";
import path from "path";
import { spawn } from "child_process";
import { app } from "electron";

// 脚本项目已随包放进 resources/moments（见 package.json build.extraResources），
// 安装后落在 process.resourcesPath/moments/；脚本以自身目录为 BASE_DIR，
// 因此 templates/ 与 config.json 的相对路径在打包版里同样成立。
const MOMENTS_DIR_NAME = "moments";
const MOMENTS_SCRIPT_NAME = "wechat_moments_poster.py";
// 随包自带的 Python 运行时（PyInstaller 冻结产物），安装后落在
// process.resourcesPath/moments-runtime/wechat_moments_poster.exe
const MOMENTS_RUNTIME_DIR_NAME = "moments-runtime";
const MOMENTS_RUNTIME_EXE_NAME = "wechat_moments_poster.exe";

// 开发态默认仍指向开发机上的原始脚本目录；仓库内副本作兜底。
const DEV_SCRIPT =
  "C:\\Users\\Administrator\\Documents\\Trae-CN\\temp_20261106_01\\wechat_moments_poster\\wechat_moments_poster.py";

function packagedMomentsDir() {
  return path.join(process.resourcesPath, MOMENTS_DIR_NAME);
}

function repoMomentsDir() {
  try {
    // 开发态 app.getAppPath() 是仓库根；打包态是 app.asar，不会走这里
    return path.join(app.getAppPath(), "resources", MOMENTS_DIR_NAME);
  } catch (_) {
    return "";
  }
}

const EXIT_CODE_REASON = {
  0: "朋友圈发表成功",
  2: "朋友圈脚本参数/配置错误",
  3: "朋友圈模板缺失（需按当前分辨率/缩放/微信版本重采模板）",
  4: "未找到微信 PC 窗口（请确认微信已登录且主窗口未最小化）",
  5: "朋友圈页面元素定位失败（模板不匹配或界面变化）",
  6: "选择图片/视频的文件对话框操作失败",
  7: "发表结果校验失败（脚本未确认发表成功）",
  8: "朋友圈发布流程超时",
  9: "用户中止",
  10: "点击安全护栏拦截（目标落在发表按钮内）",
  99: "朋友圈脚本未预期异常",
};

function resolveScriptPath() {
  const envPath = (process.env.MATRIXMEDIA_MOMENTS_SCRIPT || "").trim();
  if (envPath) return envPath;
  if (app && app.isPackaged) {
    return path.join(packagedMomentsDir(), MOMENTS_SCRIPT_NAME);
  }
  // 开发态：优先开发机原始脚本，其次仓库内副本（resources/moments）
  if (fs.existsSync(DEV_SCRIPT)) return DEV_SCRIPT;
  const repoDir = repoMomentsDir();
  return repoDir ? path.join(repoDir, MOMENTS_SCRIPT_NAME) : DEV_SCRIPT;
}

function resolvePython() {
  return (process.env.MATRIXMEDIA_MOMENTS_PYTHON || "py").trim();
}

/**
 * 随包自带的冻结运行时（目标机无需 Python）。
 * 打包版固定为 resources/moments-runtime/wechat_moments_poster.exe；不存在返回空串，
 * 调用方回退到源码模式。开发态可用 MATRIXMEDIA_MOMENTS_RUNTIME 指向本地构建产物。
 */
function resolveBundledRuntime() {
  const explicit = (process.env.MATRIXMEDIA_MOMENTS_RUNTIME || "").trim();
  if (explicit) return fs.existsSync(explicit) ? explicit : "";
  if (app && app.isPackaged) {
    const exe = path.join(
      process.resourcesPath,
      MOMENTS_RUNTIME_DIR_NAME,
      MOMENTS_RUNTIME_EXE_NAME
    );
    if (fs.existsSync(exe)) return exe;
  }
  return "";
}

function resolveTemplatesDir(templatesDir) {
  return (templatesDir || process.env.MATRIXMEDIA_MOMENTS_TEMPLATES_DIR || "").trim();
}

/**
 * 日志与截图目录：默认放「文档\MatrixMedia\data\moments-logs」。
 * 安装到 Program Files 后脚本自身目录不可写，必须显式用 --log-dir 指到用户数据目录。
 */
function resolveLogDir(logDir) {
  const explicit = (logDir || process.env.MATRIXMEDIA_MOMENTS_LOG_DIR || "").trim();
  if (explicit) return explicit;
  try {
    return path.join(app.getPath("documents"), "MatrixMedia", "data", "moments-logs");
  } catch (_) {
    return path.join(os.tmpdir(), "matrixmedia-moments-logs");
  }
}

function normalizeImages(images) {
  return (Array.isArray(images) ? images : [])
    .map((p) => String(p || "").trim())
    .filter(Boolean)
    .map((p) => path.resolve(p));
}

// 视频预检口径：微信侧真实上限未知，这里取一个保守且覆盖常见朋友圈视频的范围
const VIDEO_EXTENSIONS = ["mp4", "mov", "m4v"];
const MAX_VIDEO_BYTES = 500 * 1024 * 1024;
const MAX_VIDEO_DURATION_SEC = 60;

/** 读取文件头/尾各 8MB，避免为解析时长把整段大视频读进内存 */
function readHeadAndTail(filePath, chunkSize = 8 * 1024 * 1024) {
  const stat = fs.statSync(filePath);
  const fd = fs.openSync(filePath, "r");
  try {
    const headLen = Math.min(chunkSize, stat.size);
    const head = Buffer.alloc(headLen);
    fs.readSync(fd, head, 0, headLen, 0);
    let tail = Buffer.alloc(0);
    if (stat.size > headLen) {
      const tailStart = Math.max(headLen, stat.size - chunkSize);
      const tailLen = stat.size - tailStart;
      tail = Buffer.alloc(tailLen);
      fs.readSync(fd, tail, 0, tailLen, tailStart);
    }
    return [head, tail];
  } finally {
    fs.closeSync(fd);
  }
}

/**
 * 从 mp4/mov 的 moov->mvhd box 解析时长（秒）。
 * mvhd 结构：size(4) 'mvhd' version(1) flags(3) ...
 *   version 0：timescale 在 +16，duration 在 +20（4 字节）
 *   version 1：timescale 在 +24，duration 在 +28（8 字节）
 * 解析失败返回 null（交由调用方拒绝，不做猜测）。
 */
export function readVideoDurationSec(filePath) {
  try {
    const [head, tail] = readHeadAndTail(filePath);
    for (const buf of [head, tail]) {
      const idx = buf.indexOf("mvhd");
      if (idx < 4) continue;
      const version = buf.readUInt8(idx + 4);
      const needed = version === 1 ? idx + 36 : idx + 24;
      if (buf.length < needed) continue;
      const timescale =
        version === 1 ? buf.readUInt32BE(idx + 24) : buf.readUInt32BE(idx + 16);
      const duration =
        version === 1
          ? Number(buf.readBigUInt64BE(idx + 28))
          : buf.readUInt32BE(idx + 20);
      if (timescale > 0 && duration > 0) return duration / timescale;
    }
    return null;
  } catch (_) {
    return null;
  }
}

/**
 * 视频预检：存在性 / 扩展名 / 大小 / 时长。
 * @returns {{ok:boolean,message:string,durationSec:number|null,sizeMb:number|null,sizeBytes:number}}
 */
export function validateMomentsVideo(filePath) {
  const target = String(filePath || "").trim();
  const fail = (message) => ({
    ok: false,
    message,
    durationSec: null,
    sizeMb: null,
    sizeBytes: 0,
  });
  if (!target) return fail("请选择视频文件");
  const abs = path.resolve(target);
  try {
    if (!fs.existsSync(abs)) return fail(`视频文件不存在：${abs}`);
    if (!fs.statSync(abs).isFile()) return fail(`不是视频文件：${abs}`);
  } catch (e) {
    return fail(`无法读取视频文件：${e && e.message ? e.message : e}`);
  }
  const ext = path.extname(abs).slice(1).toLowerCase();
  if (VIDEO_EXTENSIONS.indexOf(ext) === -1) {
    return fail(
      `不支持的视频格式 ${ext ? "." + ext : "（无扩展名）"}，仅支持 mp4 / mov / m4v`
    );
  }
  let sizeBytes = 0;
  try {
    sizeBytes = fs.statSync(abs).size;
  } catch (e) {
    return fail(`无法读取视频大小：${e && e.message ? e.message : e}`);
  }
  if (sizeBytes === 0) return fail("视频文件为空（0 字节）");
  const sizeMb = sizeBytes / (1024 * 1024);
  if (sizeBytes > MAX_VIDEO_BYTES) {
    return fail(
      `视频大小 ${sizeMb.toFixed(1)}MB 超过上限 ${MAX_VIDEO_BYTES / (1024 * 1024)}MB`
    );
  }
  const durationSec = readVideoDurationSec(abs);
  if (durationSec == null) {
    return fail(
      "无法读取视频时长（文件缺少 moov/mvhd 元数据），请换一个 mp4/mov 文件"
    );
  }
  if (durationSec > MAX_VIDEO_DURATION_SEC) {
    return fail(
      `视频时长 ${durationSec.toFixed(1)} 秒超过上限 ${MAX_VIDEO_DURATION_SEC} 秒`
    );
  }
  return {
    ok: true,
    message: `视频预检通过：${path.basename(abs)}（${sizeMb.toFixed(
      1
    )}MB，${durationSec.toFixed(1)} 秒）`,
    durationSec,
    sizeMb,
    sizeBytes,
  };
}

/** 组装朋友圈文案：标题 + 正文 + 话题标签（与脚本 --text 映射） */
export function buildMomentsText({ title, description, tags } = {}) {
  const parts = [];
  const t = String(title || "").trim();
  const d = String(description || "").trim();
  if (t) parts.push(t);
  if (d && d !== t) parts.push(d);
  const tagText = Array.isArray(tags)
    ? tags.map((x) => String(x || "").trim()).filter(Boolean)
    : [];
  if (tagText.length) {
    parts.push(
      tagText
        .map((x) => (x.startsWith("#") ? x : `#${x}`))
        .join(" ")
    );
  }
  return parts.join("\n");
}

function tail(text, max = 400) {
  const s = String(text || "");
  return s.length <= max ? s : s.slice(s.length - max);
}

/**
 * 生成视频发表用的脚本配置：只调大等待/校验窗口，其余项原样沿用。
 * 视频发表后微信要转码，“处理中”期间面板状态与信息流都会变化，窗口必须留足。
 */
export function buildMomentsVideoConfig(baseConfig = {}) {
  const timing = Object.assign({}, baseConfig.timing || {});
  timing.after_publish_wait_sec = 3.0;
  timing.editor_open_timeout_sec = 30.0;
  timing.total_timeout_sec = Math.max(600.0, Number(timing.total_timeout_sec) || 0);

  const publishCfg = Object.assign({}, baseConfig.publish || {});
  publishCfg.verify_editor_closed_sec = 60.0;
  // 信息流校验放大到 60s：视频转码/上传完成前首屏可能一直不变
  publishCfg.feed_verify = Object.assign({}, publishCfg.feed_verify || {}, {
    settle_sec: 2.4,
    timeout_sec: 60.0,
  });

  return Object.assign({}, baseConfig, { timing, publish: publishCfg });
}

/**
 * 调用桌面脚本发布朋友圈。
 *
 * @param {object} options
 * @param {string} [options.text] 文案（优先于 title/description/tags 组合）
 * @param {string} [options.title]
 * @param {string} [options.description]
 * @param {string[]} [options.tags]
 * @param {string[]} [options.images] 图片绝对路径，至少一张（与 video 互斥）
 * @param {string} [options.video] 单段视频绝对路径（与 images 互斥）
 * @param {string} [options.at] 定时时间 "YYYY-MM-DD HH:mm:ss"，映射 --at
 * @param {boolean} [options.dryRun] 映射 --dry-run
 * @param {boolean} [options.noPublish] 映射 --no-publish
 * @param {string} [options.logDir]
 * @param {string} [options.templatesDir]
 * @param {number} [options.threshold]
 * @returns {Promise<{exitCode:number,status:string,message:string,logPath:string,stdoutTail:string}>}
 */
export async function publishMoments(options = {}) {
  const script = resolveScriptPath();
  const runtime = resolveBundledRuntime();
  // 打包版优先用随包运行时；只有运行时缺失才要求本机 Python + 源码脚本
  if (!runtime && !fs.existsSync(script)) {
    return {
      exitCode: 2,
      status: "failed",
      message:
        `朋友圈运行时缺失：未找到 ${MOMENTS_RUNTIME_DIR_NAME}/${MOMENTS_RUNTIME_EXE_NAME}，` +
        `也未找到源码脚本 ${script}（可用 MATRIXMEDIA_MOMENTS_RUNTIME / MATRIXMEDIA_MOMENTS_SCRIPT 指定）`,
      logPath: "",
      stdoutTail: "",
    };
  }

  const images = normalizeImages(options.images);
  const video = String(options.video || "").trim();

  if (video && images.length > 0) {
    return {
      exitCode: 2,
      status: "failed",
      message: "朋友圈同一时间只能发一段视频或一组图片，不能把视频和图片混选",
      logPath: "",
      stdoutTail: "",
    };
  }

  let media = [];
  if (video) {
    const check = validateMomentsVideo(video);
    if (!check.ok) {
      return {
        exitCode: 2,
        status: "failed",
        message: `朋友圈视频预检未通过：${check.message}`,
        logPath: "",
        stdoutTail: "",
      };
    }
    media = [path.resolve(video)];
  } else {
    if (images.length === 0) {
      return {
        exitCode: 2,
        status: "failed",
        message:
          "朋友圈至少需要一张图片或一段视频（该版本微信点相机后必须先选素材，纯文字无法发表）；请先选择",
        logPath: "",
        stdoutTail: "",
      };
    }
    const missing = images.filter((p) => !fs.existsSync(p));
    if (missing.length) {
      return {
        exitCode: 2,
        status: "failed",
        message: `朋友圈图片不存在：${missing.join("，")}`,
        logPath: "",
        stdoutTail: "",
      };
    }
    media = images;
  }

  // 朋友圈允许只发图不写字：文案为空时脚本会自动跳过文字输入，不做拦截
  const text = String(options.text || "").trim() || buildMomentsText(options);

  const logDir = resolveLogDir(options.logDir);
  try {
    fs.mkdirSync(logDir, { recursive: true });
  } catch (e) {
    return {
      exitCode: 2,
      status: "failed",
      message: `无法创建朋友圈日志目录 ${logDir}：${e && e.message ? e.message : e}`,
      logPath: "",
      stdoutTail: "",
    };
  }

  // 视频发表后微信需要转码，脚本默认 1.5s 等待 / 20s 面板校验 / 15s 信息流校验偏短；
  // 生成临时 config 调大窗口，其余项沿用脚本目录里的原配置，不改动脚本自身文件。
  let videoConfigPath = "";
  if (video) {
    try {
      const baseConfigPath = path.join(path.dirname(script), "config.json");
      const baseConfig = fs.existsSync(baseConfigPath)
        ? JSON.parse(fs.readFileSync(baseConfigPath, "utf8"))
        : {};
      const merged = buildMomentsVideoConfig(baseConfig);
      videoConfigPath = path.join(logDir, `video-config-${Date.now()}.json`);
      fs.writeFileSync(
        videoConfigPath,
        JSON.stringify(merged, null, 2),
        "utf8"
      );
    } catch (e) {
      return {
        exitCode: 2,
        status: "failed",
        message: `无法生成视频发表用的临时配置：${e && e.message ? e.message : e}`,
        logPath: "",
        stdoutTail: "",
      };
    }
  }

  // 冻结运行时不需要“脚本路径”参数；源码模式必须把 .py 作为第一个参数传给解释器
  const args = runtime ? [] : [script];
  args.push("--text", text, "--log-dir", logDir);
  // 图片与视频都走脚本的 --image 入口（微信文件对话框里选视频会进入视频编辑面板）
  media.forEach((p) => args.push("--image", p));
  if (videoConfigPath) {
    args.push("--config", videoConfigPath);
  } else if (runtime) {
    // 显式指定随包的 config.json，避免运行时目录里过期副本与仓库配置分叉
    const bundledConfig = path.join(path.dirname(script), "config.json");
    if (fs.existsSync(bundledConfig)) args.push("--config", bundledConfig);
  }
  if (options.at) args.push("--at", String(options.at));
  if (options.dryRun) args.push("--dry-run");
  if (options.noPublish) args.push("--no-publish");
  const templatesDir = resolveTemplatesDir(options.templatesDir);
  if (templatesDir) args.push("--templates-dir", templatesDir);
  if (options.threshold != null) args.push("--threshold", String(options.threshold));

  return await new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    const done = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };

    let child;
    try {
      child = spawn(runtime || resolvePython(), args, {
        cwd: runtime ? path.dirname(runtime) : path.dirname(script),
        windowsHide: true,
        env: process.env,
      });
    } catch (e) {
      done({
        exitCode: 99,
        status: "failed",
        message: `启动朋友圈脚本失败：${e && e.message ? e.message : e}`,
        logPath: logDir,
        stdoutTail: "",
      });
      return;
    }

    child.stdout.on("data", (buf) => {
      stdout += buf.toString();
    });
    child.stderr.on("data", (buf) => {
      stderr += buf.toString();
    });
    child.on("error", (err) => {
      const detail = err && err.message ? err.message : String(err);
      const message = runtime
        ? `随包朋友圈运行时启动失败（${runtime}）：${detail}；` +
          "通常是安装包缺少 resources/moments-runtime（请用 yarn build:moments-runtime 重新打包），" +
          "可查杀毒软件是否拦截了该 exe"
        : `无法执行 Python（${resolvePython()}）：${detail}；` +
          "当前为源码兜底模式，需要目标机安装 Python 3（含 py 启动器）与依赖 " +
          "mss/opencv-python/pyautogui/pywin32" +
          `（可在脚本目录执行 pip install -r requirements.txt：${path.dirname(script)}），` +
          "或用 MATRIXMEDIA_MOMENTS_PYTHON 指定解释器；" +
          "正式安装包应携带 resources/moments-runtime 自带的 exe，无需用户装 Python";
      done({
        exitCode: 99,
        status: "failed",
        message,
        logPath: logDir,
        stdoutTail: "",
      });
    });
    child.on("close", (code) => {
      const exitCode = Number.isFinite(code) ? code : 99;
      const ok = exitCode === 0;
      const reason = EXIT_CODE_REASON[exitCode] || `朋友圈脚本异常退出（退出码 ${exitCode}）`;
      // 脚本成功时最后一行通常带有日志/截图路径，原样带回去便于排查
      const logHint = (stdout.match(/logs[\\/][^\r\n]+/g) || []).slice(-1)[0] || "";
      done({
        exitCode,
        status: ok ? "success" : "failed",
        message: ok
          ? `${reason}${logHint ? `（日志：${logHint}）` : ""}`
          : `${reason}（退出码 ${exitCode}）${stderr ? "：" + tail(stderr, 200) : ""}`,
        logPath: logDir,
        stdoutTail: tail(stdout + (stderr ? "\n[stderr]\n" + stderr : "")),
      });
    });
  });
}

/**
 * Type.js 的浏览器处理器占位：朋友圈不经过 Puppeteer，正常发布由 publishVideo
 * 在开窗前分支处理。若被误调用（例如外部代码直接按平台上开窗口），明确失败而不是静默。
 */
export default function pyqBrowserHandler(page, data, transport, win, finishOnce) {
  const taskId = data && data.taskId;
  if (transport && typeof transport.reply === "function") {
    transport.reply("puppeteerFile-done", {
      taskId,
      status: false,
      message:
        "朋友圈不走浏览器自动化：请通过本机微信 PC 客户端桌面脚本发布（需 Windows 桌面会话）",
    });
  }
  if (typeof finishOnce === "function") finishOnce();
}
