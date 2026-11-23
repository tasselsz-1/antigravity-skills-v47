# Colab H200 配音工作台：前置配置与排错速查

> **唯一代码源**：请直接将 [`../resources/colab_h200_voice_dubbing.ipynb`](../resources/colab_h200_voice_dubbing.ipynb) 上传至 Google Colab 运行（本文档不再重复粘贴 5 格 Python 代码，避免与 `.ipynb` 漂移）。
>
> **省算力提示**：首次下载大权重先用 **免费 CPU 运行时** 跑完权重拉取至 `/content/drive/MyDrive/tts_studio/models/`，再切换 **H200 / L4** 运行推理 Cell；权重缓存目录与视频复刻流水线 `build-voice-colab` 完全共用，免重复下载。

---

## 1. Notebook 单元索引（与 `.ipynb` 严格对应）

| Cell | 用途 | 独立环境 / 权重持久化路径 |
| :--- | :--- | :--- |
| **Cell 1** | 挂载 Google Drive、创建 `tts_studio/{models,hf_cache,outputs}`、加载 `HF_TOKEN`、安装 `uv` 与 `huggingface_hub` | 每次重开 Colab 必先跑 |
| **Cell 2** | **`IndexTTS-2.5`** WebUI（情绪解耦 + 精准卡时长主力） | 源码 `/content/index-tts`（`uv sync`）｜权重 `Drive/.../models/IndexTTS-2.5` |
| **Cell 3** | **`Fish Audio S2 Pro`** WebUI（拟人度与长旁白天花板） | 源码 `/content/fish-speech`（`/content/envs/fish`）｜权重 `Drive/.../models/fish-s2-pro` |
| **Cell 4** | **`VoxCPM 2`** WebUI（中文捏原创声线 / 48kHz 方言母本） | 源码 `/content/VoxCPM`（`/content/envs/voxcpm`）｜缓存 `Drive/.../hf_cache` |
| **备用 Cell** | **`cloudflared`** 临时公网隧道（`serve_kernel_port_as_window(7860)` 白屏时在启动 WebUI **之前**先跑） | 输出 `https://xxxx.trycloudflare.com` |

---

## 2. `Fish Audio S2 Pro` 一次性前置配置（Gated Repo）

1. 登录 Hugging Face，打开 `https://huggingface.co/fishaudio/s2-pro` 点击同意协议。
2. 在 Hugging Face `Settings → Access Tokens` 创建 Read Token。
3. 在 Colab 左侧钥匙图标 **Secrets** 新增 `HF_TOKEN` 并开启「笔记本访问权限」，再重跑 Cell 1（严禁明文写入代码）。

---

## 3. 常见故障速查

| 现象 | 原因 | 解决方案 |
| :--- | :--- | :--- |
| 下载 `fishaudio/s2-pro` 报 `401 / 403 Forbidden` | 未同意 Gated 协议或未开启 Secrets 授权 | 同意 HF 协议 → Secrets 填 `HF_TOKEN` 并开启授权 → 重跑 Cell 1 |
| `Address already in use: 7860` | 上一个 WebUI 进程未彻底退出 | 停止上一个 Cell，执行 `!fuser -k 7860/tcp \|\| true` 释放端口 |
| `serve_kernel_port_as_window` 点开白屏 / WS 报错 | 浏览器拦截第三方 Cookie 或代理路径不兼容 | 先跑 **备用 Cell（`cloudflared`）**，等 WebUI 就绪后刷新公网链接 |
| Fish S2 Pro 首次生成卡顿约 1 分钟 | `--compile` 正在执行 `torch.compile` 图编译 | 正常现象，编译完后续每句 1~2 秒；不想等待可去掉 `--compile` |
| 重开 Colab 权重重复下载 | 未跑 Cell 1 或 `HF_HOME` 未指向 Drive | 确认已跑 Cell 1 且 Google Drive 剩余空间 ≥ 40GB |
