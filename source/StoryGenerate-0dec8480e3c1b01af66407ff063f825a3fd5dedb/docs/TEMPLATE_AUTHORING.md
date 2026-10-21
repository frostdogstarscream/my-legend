# 故事／剧情模板录入指南

## 模板是什么

模板是可复用的**叙事骨架**，不是一篇现成故事，也不是角色 Canon。
它规定节拍顺序、每个节拍的目的与写作提示、适用 Mode、约束和建议结局。
具体人物、地点、线索和对白仍由每次请求与获批大纲决定。
建议每个模板保存为一个 UTF-8 JSON 文件，文件名带版本号，例如
`spy_mystery_closed.v1.json`。

现有数据模型是 `StoryTemplateSpec`。最小必填建议：

| 字段 | 含义 |
| --- | --- |
| `template_id` | 稳定英文／数字／下划线 ID，创建后不改 |
| `version` | 正整数；修改模板时加版本，不覆盖旧版本 |
| `name` | 给编辑人员看的中文名称 |
| `supported_modes` | `general`、`mystery`、`serialized`、`viral_drama` 的子集 |
| `beats[]` | 按顺序的节拍；每项含唯一 `key`、`purpose`、可选 `guidance` 与 `required` |
| `constraints[]` | 不可违反的结构或安全约束 |
| `recommended_characters` | 建议角色数量，不是强制角色档案 |
| `tone_hints[]` | 氛围提示，不代替用户选择 |
| `defaults` | 建议默认值；用户请求仍由工作流合同校验 |
| `ending_guidance` | 如 `closed`、`cliffhanger` |
| `metadata` | 编辑标签和来源说明，不应放密钥或个人资料 |

可直接参考两份可导入样例：
[`spy_mystery_closed.v1.json`](../templates/spy_mystery_closed.v1.json) 与
[`spy_mystery_serialized.v1.json`](../templates/spy_mystery_serialized.v1.json)。
一个用于五分钟完整谍战悬疑，另一个用于长篇开场五分钟。

## 录入流程

1. 在 `templates/` 复制样例为新文件，修改 ID、版本、适用 Mode 和节拍。
   先写人物将面临的**功能性事件**，不要把固定剧情结局或整段剧本文字粘到 `guidance`。
2. 校验 JSON 与字段，不写数据库：

   ```powershell
   .venv\Scripts\python.exe scripts\import_story_template.py templates\spy_mystery_closed.v1.json
   ```

3. 人工审阅因果、内容分级与节拍可演绎性后，设置 PostgreSQL 连接并导入：

   ```powershell
   $env:DATABASE_URL = 'postgresql+psycopg://<user>:<password>@<host>:5432/<db>'
   .venv\Scripts\python.exe scripts\import_story_template.py templates\spy_mystery_closed.v1.json --install
   ```

   `--install` 才会写库；同一 `template_id + version` 重复导入会报错。
   当前脚本将人工审阅后的版本写成 `active`。请不要直接改旧行的 JSON；
   新建 `version: 2` 并保留旧版，便于复现已审批故事。

4. 创建故事时提供精确引用，例如：

   ```json
   {
     "intent": "create",
     "request_id": "spy-demo-001",
     "idempotency_key": "spy-demo-plan-001",
     "user_instruction": "一名译电员发现接头暗语里多了半个音节",
     "template_ref": {
       "template_id": "SPY_MYSTERY_CLOSED",
       "template_revision": 1
     },
     "creation_preferences": {
       "story_mode": "mystery",
       "content_form": "audio_drama",
       "target_duration_sec": 300,
       "content_rating": "teen"
     }
   }
   ```

   应用服务装配时须传入 `StoryTemplateResolver`，其仓库可为
   `PostgresStoryTemplateRepository(session)`。显式引用的模板会按 ID 和版本解析，
   在 Plan 中保存快照并进入指纹；缺少 Resolver 会明确报错，不静默忽略模板。

## 模板、故事简介和正式剧本的区别

- 模板：跨故事复用的结构数据，录入一次，多次引用。
- Plan：某次请求的具体大纲；需要用户确认。
- `StoryDelivery.preview_blurb`：生成后展示给用户的 1–2 句无剧透简介。
- `StoryDelivery.summary`：给连续性和检索使用的剧情概括，不直接替代播放前简介。
- `episodes[].scenes[].utterances[]`：完整可演绎的台词与声音语义。

当前没有 HTTP 管理端或前端模板编辑器，录入以 JSON + CLI 为准。
