# 入戏 2.0

把小说、网文章节转换为舞台剧初稿，并输出可供玩具发声、动作和灯光系统消费的 TPM 1.0 标准协议。

## 已实现

- 粘贴或导入 UTF-8 `.txt` 章节，单次最多 20,000 字符
- 导入 story.v3.1 上游故事 JSON：角色档案、关系、章节自动归一化，质量提示与错误转为待确认警告，声线档案（voice_profile）随 TPM 传递给下游
- 白话文、文言文和网文改编选项
- 10 分钟等目标时长、演员数、表演风格、舞台条件配置
- OpenAI、DeepSeek、Kimi、通义千问及自定义 OpenAI 兼容接口
- 演示模式：无 API Key 也能跑通完整流程
- 舞台剧 TXT、DOCX 导出
- TPM 1.0 JSON 导出：时间戳、标准情绪、表达意图、语调和动作
- script_lines 导出：逐行清单（line_no、旁白/对白拆分、role_code 路由、pause_after_ms、估算时长），对齐下游 Java 播放链路的「故事转剧本」契约
- 项目列表、版本保存、删除最新项目和局部重写
- SQLite 本地存储；API Key 只写入本机 `.env`

## 启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python server.py
```

打开 <http://127.0.0.1:8000>。

## 模型配置

网页左侧点击“配置大模型”。首次建议保留演示模式，确认流程后再填写 API Key。

Kimi 模型通过内部适配器固定使用 `temperature=1`，避免推理模型返回 `invalid temperature`。

## 测试

```bash
python -m unittest discover -s tests -v
```

## TPM 时间轴说明

当前输出是 `estimated` 时间轴，适合预览和设备联调。接入真实 TTS 后，应使用 TTS 返回的音频时长及字词时间戳重新编译，状态变为 `locked` 后再交给玩具正式执行。

完整标准见 [docs/TPM-1.0标准协议.md](docs/TPM-1.0标准协议.md)。

## 视觉演绎子工作流

独立模块 `app/visual_workflow`：结构化剧本、基础素材匹配、Dart规则编排、网页草案、LangGraph人工审核与版本交付；不调用AI，不生成声音。支持统一 `visual_script/1.0` 和既有手机 `2.0.0` 数据输入。

- [代码基线管理](docs/代码基线管理.md)
- [第一步实现、输入规范与验收](docs/视觉输入规范-第一步实现与验收.md)
- [首个闭环与开发计划](docs/视觉子工作流-首个可运行闭环与开发计划.md)

```sh
.visual-venv/bin/python -m app.visual_workflow.cli validate examples/visual_workflow/unified-classroom.json
sh scripts/check-visual.sh
```

子图依赖使用独立 `.visual-venv` 和 `requirements-visual.lock.txt`，不替换原服务环境。手机真机、战斗扩展及自然语言提取尚未验收。

## 声音与视觉统一预览入口

`app/preview_integration` 以独立子进程调用现有视觉 LangGraph 和同事原声音 Pipeline，检查相同源版本、事件 ID、角色和原文，再按声音实测时间对齐。可通过独立 `visual_staging/1.0` 配置明确连续画面、在场人物与入退场，导出纯画面 MP4 和外置字幕的手机预览包。同事源码与虚拟环境保持独立，声音事件不因视觉动作增加或改变。

```sh
.visual-venv/bin/python -m app.preview_integration.cli run 你的整合输入.json --root output/新运行目录
.visual-venv/bin/python -m app.preview_integration.cli status --root output/运行目录
python3 -m app.preview_integration.server --root output/运行目录/preview --port 8102
sh scripts/check-integration.sh
```

输入模板见 [request.template.json](examples/preview_integration/request.template.json)，职责、协议、恢复与交付说明见 [整合入口与交付规范](docs/视觉与声音模块-整合入口与交付规范.md)。当前只接已生成的本地声音文件，不调用模型；`preview_ready` 不代表声音审核或发布完成。Flutter 宿主接入尚未实现。
