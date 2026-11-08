<template>
  <el-dialog
    title="发表朋友圈"
    :visible.sync="visible"
    width="620px"
    append-to-body
    :close-on-click-modal="false"
    :before-close="handleBeforeClose"
  >
    <el-alert
      class="moments-hint"
      type="info"
      :closable="false"
      show-icon
      title="仅支持 Windows 桌面，且需先在本机登录微信 PC 版。更换分辨率/缩放/微信版本后需重新校准 DIP 常量，模板仅 --debug-templates 调试用。"
    />

    <el-form label-width="72px" class="moments-form">
      <el-form-item label="图片">
        <div class="picker-row">
          <el-button
            size="small"
            type="primary"
            plain
            :disabled="publishing"
            @click="pickImages"
            >选择图片（可多选）</el-button
          >
          <span class="pick-tip"
            >朋友圈可发图片（最多 9 张）或 1 段视频，二者不能混选；纯文字无法发布。</span
          >
        </div>
        <div v-if="images.length" class="image-list">
          <div v-for="(item, index) in images" :key="item.path" class="image-item">
            <img v-if="item.thumb" :src="item.thumb" class="image-thumb" />
            <div v-else class="image-thumb image-thumb-fallback">图片</div>
            <span class="image-name" :title="item.path">{{ item.name }}</span>
            <i
              class="el-icon-delete remove-icon"
              title="移除"
              @click="removeImage(index)"
            ></i>
          </div>
        </div>
        <p v-else class="empty-tip">尚未选择图片</p>
      </el-form-item>

      <el-form-item label="视频">
        <div class="picker-row">
          <el-button
            size="small"
            type="primary"
            plain
            :disabled="publishing"
            @click="pickVideo"
            >选择视频（单个）</el-button
          >
          <span class="pick-tip"
            >支持 mp4 / mov / m4v，单个视频 ≤ 500MB、≤ 60 秒；选视频会自动清空已选图片。</span
          >
        </div>
        <div v-if="video" class="video-info">
          <i class="el-icon-video-camera"></i>
          <span class="video-name" :title="video.path">{{ video.name }}</span>
          <span class="video-meta">{{ video.metaText }}</span>
          <el-button
            type="text"
            size="mini"
            :disabled="publishing"
            @click="removeVideo"
            >移除</el-button
          >
        </div>
        <p v-else class="empty-tip">尚未选择视频</p>
      </el-form-item>

      <el-form-item label="正文">
        <el-input
          v-model="text"
          type="textarea"
          :rows="5"
          maxlength="2000"
          show-word-limit
          :disabled="publishing"
          placeholder="这一刻的想法…（可留空，只发图片/视频）"
        />
      </el-form-item>
      <el-form-item label="定时发布">
        <el-switch
          v-model="scheduledPublish"
          active-text="定时"
          inactive-text="立即"
          :disabled="publishing"
        />
      </el-form-item>
      <el-form-item v-if="scheduledPublish" label="发布时间">
        <el-date-picker
          v-model="publishAt"
          type="datetime"
          value-format="yyyy-MM-dd HH:mm:ss"
          placeholder="选择年月日时分秒"
          style="width: 260px"
          :disabled="publishing"
        />
        <p class="schedule-tip">
          定时任务会立即进入发布历史，到点后自动发布；如果程序关闭错过时间，会显示任务过期。
        </p>
      </el-form-item>
    </el-form>

    <div v-if="resultMessage" class="publish-result" :class="resultOk ? 'is-ok' : 'is-fail'">
      <p class="result-text">{{ resultMessage }}</p>
      <p v-if="resultLogPath" class="result-log">日志：{{ resultLogPath }}</p>
    </div>

    <span slot="footer">
      <el-button :disabled="publishing" @click="close">取消</el-button>
      <el-button type="primary" :loading="publishing" @click="publish">{{
        publishing
          ? "正在调用本机微信…"
          : scheduledPublish
            ? "创建定时任务"
            : "发表"
      }}</el-button>
    </span>
  </el-dialog>
</template>

<script>
import { ipcRenderer } from "electron";
import moment from "moment";
import dataRequest from "@/utils/dataRequest";

const MAX_IMAGES = 9;

function baseName(filePath) {
  const parts = String(filePath || "").split(/[\\/]/);
  return parts[parts.length - 1] || String(filePath || "");
}

export default {
  name: "MomentsPublish",
  data() {
    return {
      visible: false,
      publishing: false,
      images: [],
      video: null,
      text: "",
      scheduledPublish: false,
      publishAt: "",
      resultMessage: "",
      resultOk: false,
      resultLogPath: "",
    };
  },
  methods: {
    /** 顶部工具栏「发表朋友圈」入口：清空上次内容，从零开始 */
    open() {
      this.images = [];
      this.video = null;
      this.text = "";
      this.scheduledPublish = false;
      this.publishAt = "";
      this.resultMessage = "";
      this.resultOk = false;
      this.resultLogPath = "";
      this.publishing = false;
      this.visible = true;
    },
    close() {
      if (this.publishing) return;
      this.visible = false;
    },
    handleBeforeClose(done) {
      if (this.publishing) {
        this.$message.info("正在调用本机微信，请等待本次发布结束");
        return;
      }
      done();
    },
    async pickImages() {
      if (this.images.length >= MAX_IMAGES) {
        this.$message.warning(`最多选择 ${MAX_IMAGES} 张图片`);
        return;
      }
      const picked = await ipcRenderer.invoke("dialog:openMomentImages");
      if (!Array.isArray(picked) || picked.length === 0) return;
      const exists = new Set(this.images.map((item) => item.path));
      const toAdd = [];
      for (const path of picked) {
        if (exists.has(path)) continue;
        exists.add(path);
        toAdd.push(path);
        if (this.images.length + toAdd.length >= MAX_IMAGES) break;
      }
      if (toAdd.length === 0) return;
      if (this.video) {
        this.video = null;
        this.$message.info("已切换为图片模式，视频已清空");
      }
      const thumbs = await Promise.all(
        toAdd.map((path) => ipcRenderer.invoke("moments:readThumbnail", path))
      );
      this.images = this.images.concat(
        toAdd.map((path, index) => ({
          path,
          name: baseName(path),
          thumb: thumbs[index] || "",
        }))
      );
    },
    removeImage(index) {
      this.images.splice(index, 1);
    },
    /** 选视频：单选 + 发表前预检（格式/大小/时长），选视频会清空图片 */
    async pickVideo() {
      const picked = await ipcRenderer.invoke("dialog:openMomentVideo");
      if (!picked) return;
      const check = await ipcRenderer.invoke("moments:checkVideo", picked);
      const c = check || {};
      if (!c.ok) {
        this.$message.error(c.message || "视频预检未通过，请换一个视频");
        return;
      }
      if (this.images.length > 0) {
        this.images = [];
        this.$message.info("已切换为视频模式，图片已清空");
      }
      this.video = {
        path: picked,
        name: baseName(picked),
        metaText: `${Number(c.sizeMb || 0).toFixed(1)}MB · ${Number(
          c.durationSec || 0
        ).toFixed(1)} 秒`,
      };
    },
    removeVideo() {
      this.video = null;
    },
    validatePublishAt() {
      if (!this.scheduledPublish) return "";
      const value = String(this.publishAt || "").trim();
      if (!value) return "请选择定时发布时间";
      const dt = moment(value, "YYYY-MM-DD HH:mm:ss", true);
      if (!dt.isValid()) return "定时发布时间格式应为 YYYY-MM-DD HH:mm:ss";
      if (!dt.isAfter(moment())) return "定时发布时间必须是未来时间";
      if (dt.isAfter(moment().add(1, "year"))) {
        return "定时发布时间过于遥远（最多一年），请重新选择";
      }
      return "";
    },
    /** 写入发布历史（pushData），由主进程 scheduledPublish 调度到点执行 */
    buildScheduledRecord(atText, atMs) {
      const images = this.images.map((item) => item.path);
      const video = this.video ? this.video.path : "";
      return {
        pt: "朋友圈",
        textType: "moments",
        bookName: "",
        textOtherName: "",
        selectedFile: "",
        bt: String(this.text || "").trim(),
        bq: "",
        filePath: "",
        // 兼容历史字段：首图（视频模式则记视频路径）同时写入 coverPath
        coverPath: images[0] || video || "",
        url: "",
        uploadUrl: "",
        phone: "本机微信",
        group: "",
        accountName: "本机微信",
        accountKey: "moments-local-wechat",
        partition: "persist:本机微信朋友圈",
        useragent: "",
        date: moment().format("YYYY-MM-DD"),
        publishMode: "publish",
        publishAttemptCount: 1,
        republishCount: 0,
        publishSuccessCount: 0,
        publishFailCount: 0,
        publishStatus: "scheduled",
        scheduledTask: true,
        scheduledPublishAt: atMs,
        scheduledPublishAtText: atText,
        lastPublishMessage: "等待定时发布",
        lastPublishAt: Date.now(),
        // 到点由 scheduledPublish 的「朋友圈」分支读取，与立即发表走同一个脚本
        publishOptions: {
          pyq: { images, video, text: String(this.text || "") },
        },
      };
    },
    async publish() {
      if (this.publishing) return;
      if (this.images.length === 0 && !this.video) {
        this.$message.warning(
          "请先选择图片或视频：该版本微信点相机后必须先选素材，纯文字发不了。"
        );
        return;
      }
      const scheduleError = this.validatePublishAt();
      if (scheduleError) {
        this.$message.warning(scheduleError);
        return;
      }
      this.resultMessage = "";
      this.resultLogPath = "";
      this.publishing = true;
      try {
        if (this.scheduledPublish) {
          const atText = String(this.publishAt).trim();
          const atMs = moment(atText, "YYYY-MM-DD HH:mm:ss", true).valueOf();
          await dataRequest({
            type: "add",
            fileName: "pushData",
            item: this.buildScheduledRecord(atText, atMs),
          });
          ipcRenderer.send("scheduledPublish:refresh");
          this.$message.success(`已创建朋友圈定时发布任务：${atText}`);
          this.visible = false;
          return;
        }
        const result = await ipcRenderer.invoke("moments:publish", {
          images: this.images.map((item) => item.path),
          video: this.video ? this.video.path : "",
          text: this.text,
        });
        const r = result || {};
        if (r.exitCode === 0) {
          this.$message.success(r.message || "朋友圈已发表");
          this.visible = false;
          return;
        }
        this.resultOk = false;
        this.resultMessage =
          r.message || "朋友圈发表失败，请查看日志后重试";
        this.resultLogPath = r.logPath || "";
        this.$message.error("朋友圈发表失败");
      } catch (error) {
        this.resultOk = false;
        this.resultMessage = (error && error.message) || "调用本机微信失败";
        this.$message.error(this.resultMessage);
      } finally {
        this.publishing = false;
      }
    },
  },
};
</script>

<style scoped>
.moments-hint {
  margin-bottom: 16px;
}
.moments-form {
  padding-right: 8px;
}
.picker-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.pick-tip {
  color: #909399;
  font-size: 12px;
}
.video-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafafa;
  font-size: 12px;
  color: #606266;
}
.video-name {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.video-meta {
  color: #909399;
}
.image-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 10px;
}
.image-item {
  position: relative;
  width: 132px;
  padding: 6px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafafa;
}
.image-thumb {
  width: 100%;
  height: 86px;
  object-fit: cover;
  border-radius: 4px;
  background: #f0f2f5;
}
.image-thumb-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
  font-size: 12px;
}
.image-name {
  display: block;
  margin-top: 6px;
  font-size: 12px;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.remove-icon {
  position: absolute;
  top: 4px;
  right: 6px;
  padding: 2px;
  color: #fff;
  background: rgba(0, 0, 0, 0.45);
  border-radius: 50%;
  cursor: pointer;
}
.remove-icon:hover {
  background: #f56c6c;
}
.empty-tip {
  margin: 6px 0 0;
  color: #c0c4cc;
  font-size: 12px;
}
.schedule-tip {
  margin: 6px 0 0;
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
}
.publish-result {
  margin-top: 6px;
  padding: 10px 12px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
}
.publish-result.is-ok {
  color: #67c23a;
  background: #f0f9eb;
}
.publish-result.is-fail {
  color: #f56c6c;
  background: #fef0f0;
}
.result-text {
  margin: 0;
}
.result-log {
  margin: 4px 0 0;
  color: #909399;
  word-break: break-all;
}
</style>
