# Native Image Workflow

Native 模式由 Skill 调用平台已提供的图像生成／编辑工具，Python 管线只负责准备请求和接收文件，不能在脚本中直接调用该工具。

## 请求交接

CLI 返回的 `pending_edit` 包含：

- `stage`：`clean` 或 `color`。
- `images`：必须按顺序传给图像工具的本地路径。
- `prompt`：完整提示词，不要省略或改写结构保护要求。
- `size`、`quality`、`output_format`：期望输出属性。

清理阶段只传工作画布。上色阶段第一张必须是确认后的线稿，后续才是彩色参考图。

## 模型与文件

- 不向 Native 工具传入 API 模型名；只有工具明确返回模型元数据时才记录。
- 工具未返回本地路径时，请用户下载生成图并重新上传，然后再运行 `submit-result`。
- `submit-result` 会把不同尺寸的 Native 输出等比适配到 `1152×2048` 白色工作画布。

## 模式差异

- `desktop-full` 提取并覆盖不可变线稿层，运行完整确定性 QC。
- `web-light` 只运行基础文件、尺寸和白色边角检查；必须向用户披露没有像素级线稿锁定，并在人工确认后完成。
