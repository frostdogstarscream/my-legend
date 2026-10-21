# 00 正式交付总览

StoryGenerate 将想法或已有故事转成经审批、可追溯的多角色广播剧。适用于多角色互动故事、广播剧、AI玩具的内容后端；低龄适配、真实声音和玩具交互仍需负责团队单独验收。

交付包含已有故事引擎、PostgreSQL迁移、FastAPI与独立Worker、Canon/Runtime角色接入、四操作的不可变版本、StoryDelivery与RenderManifest。本轮补齐接口手册、三种手动客户端、真实台本展示、质量审阅与成本复算、无模型HTTP联调证据。没有新增Mode/Recipe/文学评分工作流，没有新增TTS、页面或硬件实现。

|能力|已实现与证据|交付边界|
|---|---|---|
|Create|原创大纲→修订→审批→多集正文→演绎标注|每集时长是创作目标，非实测音频秒数|
|Revise|line/scene/episode/arc/ending/whole_story；结构化替换、代码合并|未授权内容保持，改变设定仍可能与后续冲突而失败|
|Continue|完整旧集+新增集、角色/事实/关系/伏笔累计|新增集数；没有播放恢复生成|
|Remix|保留/变更清单、独立story_id、精确父版本|语义变更遵循仍需正文核查|
|产品服务|持久化任务、审批快照、幂等、取消、失败重试、租约恢复|无Webhook、登录、播放写入、音频状态管理|
|角色接入|公共Canon版本、用户Runtime版本、故事快照、资产引用|素材内容、音色供应与可用性由角色/TTS团队负责|

审阅依据为实际文件，不以需求文档替代实现：

|模块|源码/合同|本轮审计重点|
|---|---|---|
|统一工作流|[workflow/service.py](../../4-引擎代码/drama_engine/workflow/service.py)、[schemas.py](../../4-引擎代码/drama_engine/workflow/schemas.py)|来源选择、继承、审批、版本保存、内部播放事件|
|四操作执行|[workflow目录](../../4-引擎代码/drama_engine/workflow)|局部范围、连续状态、二创约束与质量门|
|HTTP|[service/api.py](../../4-引擎代码/drama_engine/service/api.py)、[服务Schema](../../4-引擎代码/drama_engine/service/schemas.py)|26业务操作、认证、错误、异步边界|
|任务与Worker|[runtime.py](../../4-引擎代码/drama_engine/service/runtime.py)、[worker.py](../../4-引擎代码/drama_engine/service/worker.py)|数据库领取、wall clock租约、心跳、fencing与中断|
|数据库|[models.py](../../4-引擎代码/drama_engine/persistence/models.py)、[仓库](../../4-引擎代码/drama_engine/workflow/repository.py)、[迁移](../../alembic)|事务、版本号分配、谱系、审批快照|
|角色|[library.py](../../4-引擎代码/drama_engine/service/library.py)、[角色模型](../../4-引擎代码/drama_engine/characters/models.py)|Canon锁定、Runtime隔离、绑定与资产版本|
|交付|[delivery.py](../../4-引擎代码/drama_engine/workflow/delivery.py)、[downstream.py](../../4-引擎代码/drama_engine/service/downstream.py)|完整性、角色路由、台本、render任务|

重要纠正：Schema内有Plan EXPIRED/STALE等枚举，不代表已经有HTTP过期/取消Plan接口；`resume_playback`虽然能通过请求类型校验，正式大纲任务会失败，不能作为续写能力调用。`last_played`依赖内部播放事件，HTTP单独部署没有写入该事件的接口，联调统一用`by_id`。

没有业务负责人联系方式存于本仓库；接手团队应在自己的项目看板确定业务、角色、声音和运维负责人，不在手册中虚构联系人。
