# Provider Contract

本契约用于同步 API Provider。新增 Provider 时实现 `ImageProvider.edit_image(request) -> ImageResult`，不得修改流水线状态机、目录结构或质检规则。`native-imagegen` 由会话工具执行，通过 `pending_edit`／`submit-result` 文件交接，不伪装成 Python Provider。

## ImageEditRequest

- `images`：有序的本地图片路径。上色阶段第一张必须是已批准线稿。
- `prompt`：完整编辑提示词。
- `output_path`：Provider 必须原子写入的目标 PNG 路径。
- `model`：实际请求的模型名。
- `size`：工作尺寸，默认 `1152x2048`。
- `quality`：默认 `high`。
- `output_format`：MVP 固定 `png`。
- `background`：MVP 固定 `opaque`。
- `max_retries`：初次请求后的最大瞬时错误重试数，默认 2。

## ImageResult

- 输出路径、Provider、实际模型、请求 ID、耗时、尝试次数和可用的 usage 元数据。
- Provider 错误必须标注稳定的错误码和 `retryable`，消息不得包含凭据或授权头。

## 能力门

Provider 必须支持图像编辑、至少一张输入图、PNG 输出和 9:16 竖图。不能满足关键能力时明确失败，不得静默降低线稿保护要求。

`--model` 仅适用于 API Provider。Native 模式由平台选模；只有平台明确返回模型元数据时才覆盖 `actual_model=platform-selected`。
