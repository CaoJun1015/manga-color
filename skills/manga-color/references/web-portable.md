# Web Portable Workflow

当个人 Plugin 在 ChatGPT 网页版不可用时，把 `manga-color-web-kit.zip`、原始漫画图和彩色参考图上传到会话，并要求会话读取压缩包内的 `manga-color/SKILL.md` 后按 `$manga-color` 执行。

网页版遵循 `web-light`：

1. 使用登录账号的内置图像编辑能力清理线稿。
2. 展示原图、线稿和可获得的变化说明，等待明确确认。
3. 确认后按参考图上色，再次等待人工确认。
4. 交付两张 `1080×1920` PNG；如无法在会话内运行 Python，则用下载文件作为任务产物。
5. 明确标注 `lineart_lock=human_visual_only` 和 `HUMAN_REVIEW_ONLY`，不能声称像素级锁线。
6. 若需跨端续接，上传当前任务 ZIP；若网页端无法直接打包，先下载生成图，再在桌面端通过 `submit-result` 和 `export-task` 完成任务包。

不得要求用户在聊天中粘贴 API Key，也不得声称 Native 模式使用了特定模型。
