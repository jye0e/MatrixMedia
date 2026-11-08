"use strict";

/**
 * 朋友圈独立发布表单的 IPC（不经过账号/平台体系）。
 *
 * 表单选择本地图片或单段视频 -> 主进程调用 wechat_moments_poster.py
 * -> 返回退出码与原因。图片与视频互斥，视频在发表前先做格式/大小/时长预检。
 */

import { dialog, BrowserWindow, nativeImage } from "electron";
import { publishMoments, validateMomentsVideo } from "./upLoad/pyq.js";

export function registerMomentsIpc(ipcMain) {
  // 图片多选：朋友圈支持一次发多张图片
  ipcMain.handle("dialog:openMomentImages", async (event) => {
    const options = {
      title: "选择朋友圈图片（可多选）",
      properties: ["openFile", "multiSelections"],
      filters: [
        { name: "图片", extensions: ["jpg", "jpeg", "png", "webp", "bmp"] },
      ],
    };
    const win = BrowserWindow.fromWebContents(event.sender);
    const result = win
      ? await dialog.showOpenDialog(win, options)
      : await dialog.showOpenDialog(options);
    if (result.canceled || !result.filePaths) return [];
    return result.filePaths;
  });

  // 缩略图：直接引用本地绝对路径在打包后的 file:// 页面容易出问题，统一转 data URL
  ipcMain.handle("moments:readThumbnail", async (_event, filePath) => {
    const target = String(filePath || "").trim();
    if (!target) return "";
    try {
      const image = nativeImage.createFromPath(target);
      if (image.isEmpty()) return "";
      return image.resize({ width: 120 }).toDataURL();
    } catch (_) {
      return "";
    }
  });

  // 视频单选：mp4 / mov / m4v，不能与图片混选
  ipcMain.handle("dialog:openMomentVideo", async (event) => {
    const options = {
      title: "选择朋友圈视频（单个文件）",
      properties: ["openFile"],
      filters: [{ name: "视频", extensions: ["mp4", "mov", "m4v"] }],
    };
    const win = BrowserWindow.fromWebContents(event.sender);
    const result = win
      ? await dialog.showOpenDialog(win, options)
      : await dialog.showOpenDialog(options);
    if (result.canceled || !result.filePaths || !result.filePaths.length) {
      return "";
    }
    return result.filePaths[0];
  });

  // 发表前预检：格式 / 大小 / 时长（时长从 mp4/mov 的 mvhd 元数据解析）
  ipcMain.handle("moments:checkVideo", async (_event, filePath) => {
    return validateMomentsVideo(filePath);
  });

  ipcMain.handle("moments:publish", async (_event, payload) => {
    const images = Array.isArray(payload && payload.images)
      ? payload.images
      : [];
    const video = String((payload && payload.video) || "");
    const text = String((payload && payload.text) || "");
    return await publishMoments({ images, video, text });
  });
}
