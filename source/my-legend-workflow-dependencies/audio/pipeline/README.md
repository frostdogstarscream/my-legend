# 灵伴通用故事与声音制作线 v0.2.1

完整操作见 [v0.2.1 使用文档](../docs/guides/LangGraph使用文档_v0.2.1_20261013.md)，变更见 [版本说明](../docs/guides/v0.2.1版本说明.md)。需要 Python 3.12，使用独立环境；真实运行由配置选择模型、音色与任务策略。

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
./.venv/Scripts/python.exe -m pip install --no-deps -e .
./.venv/Scripts/lingban.exe --help
./.venv/Scripts/python.exe tools/run_offline_demo.py --root ./offline-output
```

offline-output 必须不存在；示例以合成波形运行实际 LangGraph，外部预算为零，技术通过后停在 awaiting_review，不生成真人配音、不自动发布。

本版默认指令加权阈值240，汉字2、其余1；50到100汉字是建议。每句最多两轮修正，当前模板每修订最多8次文本预占，长剧本须按规模配置。源版本、公共事件编号、素材哈希和统一时间轴共同决定审阅对象。局部修改按实际依赖重做，原始素材及旧版保留。

支持六个专业子图、逐句表演设计与缓存、独立配音及音效、媒体处理、实测排轴、草稿混音、SQLite恢复、人工审阅和批准后的版本锁定。视觉提供接口及反馈验证；StoryDelivery、caption、真实引擎执行、实时互动和任意动画仍需后续接入。

已有0.2.1七句3.1 TTS联调记录随包附带；人工听审待完成。旧pipeline README快照位于docs/archive，不能用其历史默认值代替当前文档。本包不附凭据或运行数据库。
