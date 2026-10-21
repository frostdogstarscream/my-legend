# 04 用户中途改变剧情：现有能力与组合方案

用户说“我不想继续去城堡，改为找失踪朋友”时，业务后端先询问/读取**正在听的不可变版本和位置**，判断何时改变。客户端自行保存 `{story_version_id, episode_id, scene_id, line_id, offset_ms, playback_session_id}`；这是客户端数据建议，不是新增StoryGenerate请求Schema或现成播放写入API。服务的SourceSelector可带episode_id/playback_position_ms参考，但不会保存播放事件、截断音频或自动保护已听前缀。

|时机|可用入口|保留与切换|明确限制|
|---|---|---|---|
|A 正文仍生成|取消task/job→新POST plans→审批→新job|未发布候选不是正式来源；选已有READY来源，或重新Create|不能在执行中修改已冻结审批，不能承诺复用流式半篇|
|B 已生成，第二集中途播放|Revise ending/scene/line，引用当前版本|选择尚未听过的场景边界，保持已听场景；新版本完成后由客户端切换|没有毫秒切片/音频拼接、后端播放进度写入或切换通知API|
|C 当前一集播完|若已到来源末尾用Continue；若后面已有集用Revise arc|新增结尾或替换未播放后续；旧版本一直可读|Continue不在任意集后插入，不删除已缓存后续|
|D 大纲未批准|POST plans/id/revisions|新revision确认后重新审批|APPROVED不能同Plan反馈|

## A 生成中

GET任务确认是PlanningTask还是正式Job。分别POST `/v1/tasks/{task_id}/cancel`或`/v1/jobs/{job_id}/cancel`，无body；轮询到CANCELLED。若取消前已SUCCEEDED，以实际成功版本决定是否Revise。CANCEL_REQUESTED不代表供应商撤单；取消不退款、已发请求仍可能计费。

重新POST `/v1/plans`：若从零生成，用Create原请求+新要求+全新request_id/key；若有已发布来源，用Revise引用该版本。重新查看Plan范围并approve。不能把取消的Job retry为新剧情，也不能拿未发布候选的伪version_id当来源。客户端展示“旧版本可继续播放，新的方向准备中”，需要用户选择是否等候，由业务团队实现。

## B 第二集中途：选择未播放后缀

假设已缓存完整四集，第二集有sceneA/B/C/D，用户正在B。客户端可选择让B播放完，更新位置到B结束；只改C/D以及第三、四集。先GET当前StoryDelivery检查真实ID与场景数。

```json
{
  "intent": "revise",
  "request_id": "direction-change-new-uuid",
  "idempotency_key": "direction-plan-new-uuid",
  "source_selector": {"type": "by_id", "story_version_id": "CURRENT_READY_VERSION"},
  "user_instruction": "从第二集尚未播放的后缀开始，放弃前往城堡，改为寻找失踪朋友；所有已听场景保持不变，保留人物身份和已知事实。",
  "edit": {
    "scope": "ending",
    "target": {"episode_id": "ACTUAL_EPISODE_2", "scene_ids": ["ACTUAL_SCENE_C", "ACTUAL_SCENE_D"]},
    "instruction": "改写第二集末尾连续场景并调整之后剧情，让寻找朋友的决定有现有事实支撑。",
    "downstream_policy": "replan_affected"
  }
}
```

这是**带占位符的组合请求**，不是已执行样例。需要当前集连续末尾场景后缀；通过prepare→poll→GET Plan，确认批准范围包含第二集C/D以及第三、四集，并确认A/B没有进入重规划。再approve→poll→GET新版本，逐项比较已听A/B完整JSON、检查新方向、parent与来源。若审批扩展包含已听内容，不能直接批准。

`make_request.py revise ... --scope ending --episode 2 --scene 3 --downstream-policy replan_affected`可从真实下载JSON生成后缀ID。场景数不足或用户正在最后场景时，本方案不适用。不能把整个当前场景重写还宣称前半段原封不动。一个明确未播放台词可用line修改，但不提供任意多行范围的原子截断续写。需要精确截断者属于后续客户端/音频协议开发，不列为当前交付能力。

`branch_from_here`仍保留来源后续集，不等于删掉第三四集。修改影响其事实时宜使用受审阅的`replan_affected`；保留后续而冲突可能失败，不能强制绕过质量门。

客户端/TTS收到新版本后自行：对比版本→按新manifest排队合成未播放内容→等待安全切点→切换playlist→保存新版本位置。服务没有Webhook/推送切换接口，业务后端轮询Job通知自己客户端。旧音频是否复用由下游依据文本/performer/标注/资产修订和上下文自行计算，不能只凭line_id复用。保留旧story_version_id以便回看和回滚播放选择，不能修改旧版本正文。

## C 一集播完

若来源仅有两集，第二集播放完后要新方向：Continue `target_episodes:1`，新第3集承接事实和关系。若来源已经有四集且只听完第2集，则Continue会追加第5集，**不会替换第3/4集**。应Revise arc，target `episode_ids:[第三集实际ID,第四集实际ID]`，新方向写入instruction，重新审批与生成。工具：

```sh
python tools/handoff/make_request.py revise delivery.local.json "从第三集改去寻找失踪朋友，已播放前两集保持不变" --scope arc --episode 3 --through-episode 4 --output operation.local.json
```

若只想永久独立的世界设定分支用Remix，但Remix会重创整部目标故事，不天然保存已播放前缀。不要为了中途改方向误用Remix。

## D 未审批

GET Plan→POST revisions `{expected_revision,feedback,idempotency_key}`→poll task→GET新revision→用户确认→approve新fingerprint。客户端标记旧审批卡片失效，防止点旧revision。Plan的EXPIRED枚举不能当作自动过期服务承诺。

验证责任：本服务验证版本、修改范围、保留内容及生成要求；客户端验证自己的播放进度和安全切点；TTS团队验证新音频可用并正确路由。没有独立真人的听中途切换试用，也没有真实音频/设备验收，因此本章是可实现的现有接口组合与明确边界，不是完整播放功能已交付的证明。
