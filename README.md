# Manga Color

`manga-color` 是一个可在 Codex 桌面端和 ChatGPT 网页版使用的 skills-only Plugin，用于把黑白漫画面板中的主要人物提取成干净线稿，再按角色彩色参考图（或用户明确允许的角色配色推断）上色。

它适合制作漫画黑白／彩色前后对比素材，默认使用登录账号可用的内置 ImageGen，不需要 API Key。

## 功能

- 清除对白框、气泡、文字、拟声词、其他人物和原始背景。
- 保留目标人物的姿势、五官、服装轮廓和可见线条；遮挡处只允许补齐最短局部线段。
- 每张线稿都必须人工确认，确认前不会进入上色。
- 默认生成 9:16 竖版工作图（`1152×2048`），最终输出 `1080×1920` PNG。
- `desktop-full` 模式会提取不可变线稿层，确定性覆盖到彩色底层，并执行完整 QC。
- `web-light` 模式适用于网页版或无持久工作区，执行基础检查并明确标注不保证像素级锁线。
- 任务可导出为 ZIP，在桌面端和网页版之间恢复或升级执行模式。
- 可选使用 OpenAI API 精确调用 `gpt-image-2`；API Key 只从环境变量读取。

## 在 Codex 中使用

安装后新建一个任务，直接输入：

```text
使用 $manga-color 处理我上传的黑白漫画图和角色彩色参考图。
```

上传：

1. 一张黑白漫画原图；
2. 一至三张角色彩色参考图。

如果没有彩色参考图，Skill 会暂停询问。只有明确同意后，才可以按角色名推断配色；角色有多个版本时，先确认版本。

## 工作流程

```text
素材校验
  → 线稿清理
  → REVIEW_LINEART（展示原图、线稿和候选变化图）
  → 用户确认
  → 角色配色上色
  → REVIEW_QC（彩色结果复核）
  → 1080×1920 最终输出
```

红色候选变化图只是暗像素差异的辅助标记，不代表自动判断补线正确。看到人物位置、比例、线条或补线不符合预期时，应选择“重新清理”；确认后才选择“继续上色”。

## CLI 使用

CLI 位于 `skills/manga-color/scripts/manga_color.py`，标准输出为单个 JSON 对象。将下面的 `<SKILL_ROOT>` 替换为本仓库的 `skills/manga-color` 路径。

### Native ImageGen（默认）

创建任务并准备清理请求：

```powershell
python <SKILL_ROOT>/scripts/manga_color.py start `
  --source <原图.png> `
  --reference <彩色参考图.png> `
  --character-hint "FGO 成年 Caster 达芬奇" `
  --provider native `
  --profile desktop-full `
  --output-root .\manga-coloring
```

没有参考图且已明确允许推断时，添加 `--allow-inferred-palette`。Native 模式创建后会停在 `AWAITING_CLEAN_RESULT`，返回的 `pending_edit` 中包含有序输入图和完整提示词。用内置 ImageGen 生成后提交：

```powershell
python <SKILL_ROOT>/scripts/manga_color.py submit-result `
  --task <任务目录> `
  --stage clean `
  --image <生成的线稿.png>
```

人工确认线稿后：

```powershell
python <SKILL_ROOT>/scripts/manga_color.py approve-lineart --task <任务目录>
```

再按新的 `pending_edit` 调用内置 ImageGen，并提交彩色结果：

```powershell
python <SKILL_ROOT>/scripts/manga_color.py submit-result `
  --task <任务目录> `
  --stage color `
  --image <生成的彩色图.png>
```

### OpenAI API（可选）

只有在用户明确要求精确模型且本机配置了 Key 时使用：

```powershell
$env:OPENAI_API_KEY = "在本机环境变量中配置，不要粘贴到聊天"
python <SKILL_ROOT>/scripts/manga_color.py start `
  --source <原图.png> `
  --reference <彩色参考图.png> `
  --provider openai `
  --model gpt-image-2 `
  --profile desktop-full
```

API 模式会同步执行清理和上色，但仍然会在 `REVIEW_LINEART` 停下来等待人工确认。API Key 不会写入 manifest、日志或任务 ZIP。

### 复核、完成与重试

```powershell
python <SKILL_ROOT>/scripts/manga_color.py retry-clean --task <任务目录> --feedback "保留原图人物位置，不要补画未显示的下半身"
python <SKILL_ROOT>/scripts/manga_color.py retry-color --task <任务目录> --feedback "保持线稿不变，调整发色和服装配色"
python <SKILL_ROOT>/scripts/manga_color.py finalize --task <任务目录>
```

每个阶段最多重试两次。`web-light` 必须人工确认后加 `--human-approved` 才能完成：

```powershell
python <SKILL_ROOT>/scripts/manga_color.py finalize `
  --task <任务目录> `
  --human-approved `
  --comparison-layout none
```

### 状态与跨端任务包

```powershell
python <SKILL_ROOT>/scripts/manga_color.py status --task <任务目录>
python <SKILL_ROOT>/scripts/manga_color.py export-task --task <任务目录> --output .\manga-color-task.zip
python <SKILL_ROOT>/scripts/manga_color.py import-task `
  --bundle .\manga-color-task.zip `
  --output-root .\manga-coloring `
  --profile desktop-full
```

导入会校验 SHA-256、防止 Zip Slip 和符号链接，并创建新的任务 ID，不覆盖已有任务。网页版个人 Plugin 不可用时，可上传 `dist/manga-color-web-kit.zip`、任务 ZIP 和待处理图片到新会话继续。

## 输出目录

默认任务目录为 `./manga-coloring/<task-id>/`，包含输入副本、中间图、QC 报告和原子更新的 `00_manifest.json`。完成后：

```text
final/lineart_1080x1920.png
final/colored_1080x1920.png
```

只有指定 `side_by_side` 或 `top_bottom` 时才生成对比图。

## 开发与测试

```powershell
python -m unittest discover -s skills/manga-color/tests -v
python -X utf8 <path-to-skill-creator>/scripts/quick_validate.py skills/manga-color
python -X utf8 <path-to-plugin-creator>/scripts/validate_plugin.py .
```

运行 Native 模式不需要安装 OpenAI SDK；API 模式需要安装 `requirements.txt` 中的依赖。当前实现不提供批量处理、云端同步、视频生成或联网搜索角色参考图。

## 隐私与限制

- 默认 Native 模式记录平台实际返回的模型；未暴露模型名时记录 `platform-selected`，不会虚构为 `gpt-image-2`。
- `desktop-full` 的线稿锁定来自本地不可变图层复合；`web-light` 只保证人工视觉一致，不保证像素级线稿一致。
- 用户应确认对上传漫画和参考图拥有合法处理与发布权限。

