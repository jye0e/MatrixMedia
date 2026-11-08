#!/usr/bin/env node
"use strict";

/**
 * 构建「朋友圈脚本」的自带运行时（Windows）。
 *
 * 目标：安装包不依赖目标机的 Python / pip，也不需要用户装 opencv 等依赖。
 * 做法：用 PyInstaller 把 resources/moments/wechat_moments_poster.py 冻成
 * onedir 目录（exe + _internal），产物落到 resources/moments-runtime/，
 * 由 package.json 的 build.extraResources 原样带进安装包的 resources/moments-runtime/。
 *
 * 为什么用 onedir 而不是 onefile：
 *   - onefile 每次启动都要把上百 MB 解包到临时目录（额外 3~6s，且易被杀软反复扫描）；
 *   - onedir 启动无解包开销（本机实测 --help 2.1~2.8s，源码模式 3.8~4.6s），
 *     体积与 onefile 接近（onefile 仅多一层压缩）；
 *   - Electron 安装包本来就有独立 resources 目录，多一个文件夹不增加安装复杂度。
 *
 * 构建机要求：Python 3.10+ 且已安装 pyinstaller（pip install pyinstaller）。
 * 可用 MOMENTS_BUILD_PYTHON 指定构建用解释器路径。
 */

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const repoRoot = path.join(__dirname, "..");
const momentsDir = path.join(repoRoot, "resources", "moments");
const scriptPath = path.join(momentsDir, "wechat_moments_poster.py");
const outDir = path.join(repoRoot, "resources", "moments-runtime");
const workDir = path.join(repoRoot, "build", "pyinstaller");
const distDir = path.join(workDir, "dist");
const appName = "wechat_moments_poster";

const HIDDEN_IMPORTS = [
  "pyautogui",
  "pymsgbox",
  "pytweening",
  "pyscreeze",
  "pyrect",
  "mouseinfo",
  "pygetwindow",
];

// 运行时定位全部由 DIP 常量完成，OpenCV 只服务模板对照；排除它可省 ~112 MB
// （cv2.pyd 82 MB + opencv_videoio_ffmpeg 29 MB）。tkinter 也未使用。
const EXCLUDED_MODULES = ["cv2", "tkinter", "_tkinter", "matplotlib", "pandas", "scipy"];

function log(msg) {
  console.log(`[moments-runtime] ${msg}`);
}

function fail(msg) {
  console.error(`[moments-runtime] 失败：${msg}`);
  process.exit(1);
}

function candidatePythons() {
  const list = [];
  if (process.env.MOMENTS_BUILD_PYTHON) list.push(process.env.MOMENTS_BUILD_PYTHON);
  list.push("python", "py");
  return list;
}

function pickPython() {
  for (const candidate of candidatePythons()) {
    const probe = spawnSync(candidate, ["-c", "import sys;print(sys.version_info[0])"], {
      encoding: "utf8",
      windowsHide: true,
    });
    if (probe.status === 0 && String(probe.stdout).trim() === "3") {
      return candidate;
    }
  }
  return "";
}

function rmDir(target) {
  if (fs.existsSync(target)) fs.rmSync(target, { recursive: true, force: true });
}

function dirSizeMb(target) {
  let total = 0;
  const walk = (p) => {
    for (const entry of fs.readdirSync(p, { withFileTypes: true })) {
      const full = path.join(p, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile()) total += fs.statSync(full).size;
    }
  };
  if (fs.existsSync(target)) walk(target);
  return total / (1024 * 1024);
}

function main() {
  if (!fs.existsSync(scriptPath)) fail(`脚本不存在：${scriptPath}`);

  const python = pickPython();
  if (!python) {
    fail(
      "找不到 Python 3。构建机需要安装 Python 3.10+ 与 pyinstaller（pip install pyinstaller），" +
        "或用 MOMENTS_BUILD_PYTHON 指定解释器。"
    );
  }

  const check = spawnSync(python, ["-m", "PyInstaller", "--version"], {
    encoding: "utf8",
    windowsHide: true,
  });
  if (check.status !== 0) {
    fail(
      `${python} 未安装 PyInstaller（pip install pyinstaller）。` +
        "这是构建机一次性依赖，不会进入安装包。"
    );
  }
  log(`构建解释器：${python}（PyInstaller ${String(check.stdout).trim()}）`);

  rmDir(distDir);
  fs.mkdirSync(workDir, { recursive: true });

  const args = [
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--noupx",
    "--name",
    appName,
    "--distpath",
    distDir,
    "--workpath",
    path.join(workDir, "work"),
    "--specpath",
    workDir,
    "--log-level",
    "WARN",
  ];
  for (const name of HIDDEN_IMPORTS) args.push("--hidden-import", name);
  for (const name of EXCLUDED_MODULES) args.push("--exclude-module", name);
  args.push(scriptPath);

  log("开始冻结（PyInstaller onedir，首次约 1~3 分钟）...");
  const startedAt = Date.now();
  const build = spawnSync(python, args, {
    cwd: momentsDir,
    stdio: "inherit",
    windowsHide: true,
  });
  if (build.status !== 0) fail(`PyInstaller 退出码 ${build.status}`);
  log(`冻结完成，耗时 ${((Date.now() - startedAt) / 1000).toFixed(1)}s`);

  const builtDir = path.join(distDir, appName);
  const builtExe = path.join(builtDir, `${appName}.exe`);
  if (!fs.existsSync(builtExe)) fail(`未找到构建产物：${builtExe}`);

  rmDir(outDir);
  fs.mkdirSync(outDir, { recursive: true });
  for (const entry of fs.readdirSync(builtDir)) {
    fs.cpSync(path.join(builtDir, entry), path.join(outDir, entry), { recursive: true });
  }

  // exe 以自身目录为 BASE_DIR：配置与模板随运行时一起放，保证脱离 Electron 也能单跑
  fs.copyFileSync(path.join(momentsDir, "config.json"), path.join(outDir, "config.json"));
  const templatesSrc = path.join(momentsDir, "templates");
  if (fs.existsSync(templatesSrc)) {
    fs.cpSync(templatesSrc, path.join(outDir, "templates"), { recursive: true });
  }

  const sizeMb = dirSizeMb(outDir);
  log(`产物：${path.join(outDir, `${appName}.exe`)}`);
  log(`运行时体积：${sizeMb.toFixed(1)} MB（含 _internal 依赖与模板）`);
}

main();
