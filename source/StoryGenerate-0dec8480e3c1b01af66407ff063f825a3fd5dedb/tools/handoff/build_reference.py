"""Generate exhaustive API field/endpoint reference from the checked-in OpenAPI.

Business explanations are curated here; schemas and archived responses are linked,
never presented as newly executed requests.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/handoff"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    spec = json.loads((ROOT / "docs/phase11/openapi.json").read_text(encoding="utf-8"))
    write_json(OUT / "contracts/openapi.json", spec)
    original = ROOT / "6-实测产物/phase11_live_20261015_v3"
    for op in ("create", "revise", "continue", "remix"):
        request = json.loads((original/op/"request.json").read_text(encoding="utf-8"))
        # Preserve real archived identifiers: replaying against another deployment requires replacement.
        write_json(OUT/f"examples/{op}.real.json", request)
    characters = {"characters": [
        {"character_id": cid, "expected_version": 0,
         "canon": {"name": name, "species": species},
         "runtime_defaults": {"personality": [personality]},
         "assets": {"voice": {"asset_id": "demo-"+cid, "revision": "1", "performer_id": "voice-"+cid}}}
        for cid, name, species, personality in [("bear", "小熊", "熊", "热心但容易冒失"), ("rabbit", "小兔", "兔", "细心且幽默")]]}
    # Canon schema has identity fields; validate generated data in verify.py.
    write_json(OUT/"examples/forest.characters.json", characters)
    forest = {"intent": "create", "request_id": "forest-create-REPLACE-UUID", "idempotency_key": "forest-plan-REPLACE-UUID",
              "user_instruction": "讲一个小熊和小兔子在森林里寻找神秘宝箱的故事，要幽默、有反转；用清楚温和的语言。此请求按当前支持的14-17年龄档演示，不宣称适用于低龄儿童。",
              "creation_preferences": {"story_mode": "general", "genre": "森林冒险", "tone_style": "幽默温暖", "audience_band": "14-17", "content_rating": "teen", "target_episodes": 2, "target_duration_sec": 120, "ending_preference": "open"},
              "characters": {"selected_character_ids": ["bear", "rabbit"], "role_bindings": [
                  {"character_id": cid, "role_id": cid, "performer_id": "voice-"+cid} for cid in ("bear", "rabbit")]}}
    write_json(OUT/"examples/forest.create.json", forest)
    write_json(OUT/"examples/plan.revision.json", {"expected_revision": 1, "feedback": "不要让他们得到金银宝藏，改成最后发现宝箱里是他们小时候的回忆。", "idempotency_key": "feedback-REPLACE-UUID"})
    write_json(OUT/"examples/plan.approval.json", {"expected_revision": 2, "fingerprint": "0"*64, "idempotency_key": "approval-REPLACE-UUID"})
    write_json(OUT/"examples/runtime.patch.json", {"expected_revision": 0, "patch": {"dialogue_style": "更口语化，避免生僻词"}})
    descriptions = {
        "POST /v1/plans": ("准备四操作大纲", "来源版本必须属于当前 principal；绑定角色已导入。请求 intent 决定分支；完整四操作真实请求见下方。", "202 TaskView；保存 task_id，轮询到 SUCCEEDED 后从 result 获取 plan_id。", "principal+body.idempotency_key；相同键相同内容重用任务，变更内容409 IDEMPOTENCY_KEY_CONFLICT；request_id 重用但新键可触发 REQUEST_ID_CONFLICT。", "create/submit_before_restart.json"),
        "GET /health": ("数据库健康", "匿名；只确认数据库可连接，不证明 Worker、模型、TTS 正常。", "200 {\"status\":\"ready\",\"storage\":\"postgresql\"}", "只读，无业务幂等键。", None),
        "GET /v1/tasks/{task_id}": ("大纲任务轮询", "使用 prepare/revisions 返回的 task_id。", "200 TaskView；FAILED 仍是HTTP200，查看 error_code/error_message。", "只读；跨 principal 返回404。", "create/planning_task.json"),
        "POST /v1/tasks/{task_id}/cancel": ("取消大纲任务", "QUEUED/RUNNING 可取消。", "200 TaskView；排队直接 CANCELLED，运行中 CANCEL_REQUESTED 后等待终态。", "CANCELLED 可重复取消；CANCEL_REQUESTED 重复调用可能409 TASK_NOT_CANCELLABLE，请轮询。", None),
        "POST /v1/tasks/{task_id}/retry": ("显式重试失败大纲", "只允许 FAILED；CANCELLED 不可重试。", "202 同一 task_id 回到 QUEUED；再次轮询。", "无请求体；重复调用若已QUEUED返回409，不自动重试计费调用。", None),
        "GET /v1/plans/{plan_id}": ("获取当前大纲", "大纲任务 SUCCEEDED 后获取 plan_id。", "200 PlanPreview；展示 outline、角色快照、revision、fingerprint 和影响范围。", "只读；审批前必须重新获取最新内容。", "create/revised_plan.json"),
        "GET /v1/plans/{plan_id}/revisions": ("查询大纲历史", "当前 principal 可访问该 Plan。", "200 PlanPreview[]，按 revision 升序。", "只读，历史快照不可变。", None),
        "POST /v1/plans/{plan_id}/revisions": ("反馈生成新大纲修订", "Plan 必须 AWAITING_APPROVAL；expected_revision 为最新值。APPROVED 不支持原地修改。", "202 TaskView；完成后重新GET Plan，核对revision增加，再审批。", "大纲队列幂等键与prepare共享命名空间；过期修订409 STALE_PLAN。", "create/revision_submit.json"),
        "POST /v1/plans/{plan_id}/approve": ("审批并原子创建正文任务", "人工确认最新 Plan；提交 expected_revision 和 fingerprint（取自 plan_fingerprint）。角色快照不能过期。", "202 JobView；审批与 QUEUED Job 在同一事务提交，保存job_id。", "审批幂等键独立于大纲队列；过期 revision/fingerprint409，角色版本变化409。", "create/approval.json"),
        "POST /v1/plans/{plan_id}/generate": ("查询已审批的生成任务", "APPROVED 且已有关联 Job。没有请求体。", "202 JobView；只取审批时的Job，不另建生成任务。", "未审批409 PLAN_APPROVAL_REQUIRED；重复调用不多次发布。", "create/approval_idempotent.json"),
        "GET /v1/jobs/{job_id}": ("正文任务轮询", "审批返回的 job_id。", "200 JobView；SUCCEEDED后用result_story_version_id取正文。", "只读；进度是执行阶段提示，不是剩余时间保证。", "create/generation_attempt2.json"),
        "POST /v1/jobs/{job_id}/cancel": ("取消正式生成", "排队/运行/校验/取消中可调用。", "200 JobView；等待 CANCELLED，不能把202/200理解为模型已停止。", "CANCELLED重复调用可返回同状态；成功/失败任务409 JOB_NOT_CANCELLABLE。", None),
        "POST /v1/jobs/{job_id}/retry": ("按原审批快照重试", "只允许 FAILED；先阅读错误和成本记录。", "202 JobView，同job_id再次排队；attempt_history保留失败记录。", "无请求体；CANCELLED不可重试。若变更要求，必须新Plan而非retry。", "create/retry.json"),
        "GET /v1/stories": ("列出版本摘要", "当前 principal。", "200 数组；每行是版本，多行可共享story_id。", "只读；limit/offset分页，不保证并发写入期间稳定游标。", None),
        "GET /v1/stories/{story_id}/versions": ("同故事历史版本", "已发布story_id。", "200 StoryDelivery[]，version_no升序；不存在404。", "历史派生版本号按数据库串行分配，不能推定parent.version_no+1。", None),
        "GET /v1/story-versions/{version_id}": ("读取完整故事", "Job已SUCCEEDED，保存result_story_version_id。", "200 StoryDelivery 1.0；完整旧集与新集，不是增量事件流。", "不可变版本；无ETag写入协议。", "continue/delivery.json"),
        "GET /v1/story-versions/{version_id}/lineage": ("查询来源和衍生分支", "可访问version_id。", "200 {root_requested_version_id, versions:[{story_id,story_version_id,version_no,parent_story_version_id,lineage_type}]}。", "只读；遍历连通祖先、后代和同源分支，包含不同story_id的Remix。", None),
        "GET /v1/story-versions/{version_id}/export": ("三种导出", "READY版本；format=json/script/characters，默认json。", "json为完整StoryDelivery；script为text/plain完整台本；characters为角色映射JSON。", "只读；OpenAPI此动态响应不完全描述各媒体类型，按format解析。", "create/export.json"),
        "GET /v1/story-versions/{version_id}/render-manifest": ("导出演绎队列", "READY且通过完整性校验。", "200 RenderManifest；有序speech/cue任务，无真实音频。", "同版本同位置task_id稳定，新版本会改变。", "create/render.json"),
        "POST /v1/story-versions/{version_id}/simulate-tts": ("模拟消费验证", "READY；不调用真实TTS。无请求体。", "200 simulation_only=true；audio_state仍NOT_SYNTHESIZED。", "确定性契约验证，可重复；不写音频状态。", "create/simulation.json"),
        "POST /v1/characters/import": ("角色批量导入与版本更新", "admin；批内character_id唯一；首次expected_version=0。", "200 CharacterView[]；整批事务，错误不部分提交。", "expected_version乐观并发；固定Canon不可覆写；无业务幂等键，重放已成功导入会版本冲突。", None),
        "GET /v1/characters": ("公共角色目录", "已认证；Canon目录跨principal共享，Runtime不共享。", "200 CharacterView[]；active角色最新版本，limit/offset分页。", "只读。", None),
        "GET /v1/characters/{character_id}": ("角色版本详情", "已认证；version_number省略取最新，提供>=1取历史。", "200 CharacterView；未知角色/版本404。", "只读；故事已保存快照不受后续库变更影响。", None),
        "GET /v1/characters/{character_id}/versions": ("角色Canon版本历史", "已认证；角色存在。", "200 CharacterView[]，按version升序。", "只读；不是用户Runtime版本。", None),
        "GET /v1/characters/{character_id}/runtime": ("用户动态参数历史", "当前principal；角色已导入。", "200 RuntimeView[]，revision升序；未个性化时可为空。", "principal隔离，空历史下首次PATCH expected_revision=0。", None),
        "PATCH /v1/characters/{character_id}/runtime": ("追加个性化修订", "expected_revision为当前最大值或首次0；只允许Runtime字段。", "200 RuntimeView；保存新revision。", "并发冲突409；Canonical身份字段不得放进patch，额外字段422。", None),
    }
    lines = ["# 02 完整 HTTP API 接口手册", "", "服务版本11.0；依据基线 eeb99ae 的实际路由和 OpenAPI。本文由 `tools/handoff/build_reference.py` 生成字段清单，业务解释人工维护。", "",
        "[机器可读OpenAPI](contracts/openapi.json) · [操作示例](03-manual-demos.md) · [ID、状态与错误处理](07-client-integration.md)", "",
        "所有 `/v1` 路由使用 `Authorization: Bearer <环境注入密钥>`；仅导入角色要求admin。`Content-Type: application/json` 用于有body的POST/PATCH。不要在body写principal；服务器从凭据映射身份。所有响应有服务器生成的 `X-Request-ID`，不同于业务request_id。没有 `Idempotency-Key` 请求头协议。", "",
        "[26接口完整成功响应索引](examples/endpoint-success-examples.json)按Method+Path查找，明确区分executed_no_model_http、historical_real_model_http_chain和schema_example_NOT_EXECUTED。后者仅两项（大纲失败重试/正文取消成功态），不能当本轮实测；失败态另有本轮真实HTTP探测。response_reference从仓库根取档案，再读json_pointer。完整字段类型见本文组件表。GET/无body动作的请求JSON为“不适用”；URL即完整请求。", "",
        "统一异常完整示例：", "```json", '{"error":{"code":"STALE_PLAN","message":"STALE_PLAN","request_id":"server-correlation-id","details":[]}}', "```", "",
        "通用错误：401 AUTH_REQUIRED；403 ADMIN_REQUIRED（仅导入）；404 NOT_FOUND（含无访问权）；409 幂等/修订/状态冲突；422 SCHEMA_VALIDATION_FAILED 或领域输入码；503 SERVICE_UNAVAILABLE。所有接口均须处理网络超时；超时不代表任务取消。HTTP200的任务FAILED仍是业务失败。", "",
        "## 完整路由清单", "", "|操作|用途|", "|---|---|"]
    count = 0
    for path, methods in spec["paths"].items():
        for method, item in methods.items():
            if method.upper() not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
                continue
            key = method.upper()+" "+path
            assert key in descriptions, key
            count += 1
            lines.append(f"|`{key}`|{descriptions[key][0]}|")
    lines += ["", f"合计 **{count}个 method/path 操作、{len(spec['paths'])}个路径**。额外匿名元数据路由：GET `/docs`（Swagger HTML）、`/redoc`（ReDoc HTML）、`/openapi.json`（完整schema）、`/docs/oauth2-redirect`（Swagger OAuth辅助HTML，当前认证仍是Bearer）。不提供登录、播放进度写入、音频合成、Webhook或任务recover HTTP接口。", ""]
    for path, methods in spec["paths"].items():
        for method, item in methods.items():
            if method.upper() not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
                continue
            key = method.upper()+" "+path
            title, before, after, idem, evidence = descriptions[key]
            lines += [f"## {key} — {title}", "", f"**权限**：{'匿名' if path == '/health' else 'admin Bearer' if path == '/v1/characters/import' else 'Bearer，principal隔离的资源需属于调用人'}。", "", f"**前置条件**：{before}", "", "**参数**：", "", "|位置/名称|类型与限制|必填/默认|业务含义|", "|---|---|---|---|"]
            for p in item.get("parameters", []):
                schema = p.get("schema", {})
                meaning = {"task_id":"大纲队列ID", "job_id":"正文队列ID", "plan_id":"大纲ID", "story_id":"同故事分组", "version_id":"不可变故事版本", "character_id":"角色库ID", "version_number":"指定Canon历史版本；省略取最新", "limit":"每页条数", "offset":"跳过条数", "format":"json全文/script台本/characters映射"}.get(p["name"], p["name"])
                lines.append(f"|{p['in']}/{p['name']}|`{json.dumps(schema, ensure_ascii=False)}`|{'必填' if p.get('required') else '可选'}|{meaning}|")
            body = item.get("requestBody")
            if body:
                schema = body["content"]["application/json"]["schema"]
                lines += [f"|body|`{json.dumps(schema, ensure_ascii=False)}`|{'必填' if body.get('required') else '可选'}|下方完整示例；所有字段见组件表|", "", "**完整请求JSON**："]
                ex = "[四操作真实请求](examples/create.real.json)、[Revise](examples/revise.real.json)、[Continue](examples/continue.real.json)、[Remix](examples/remix.real.json)；异地重放须替换来源/角色及全新ID。" if key == "POST /v1/plans" else "[Plan反馈](examples/plan.revision.json)" if key.endswith("/revisions") else "[审批](examples/plan.approval.json)，fingerprint必须用刚读取的大纲值替换64个0。" if key.endswith("/approve") else "[角色导入](examples/forest.characters.json)" if key.endswith("/import") else "[Runtime更新](examples/runtime.patch.json)"
                lines += [ex]
            else:
                lines += ["", "**完整请求**：上述Method和URL替换Path参数，加认证头；没有请求JSON，不发送`{}`。"]
            lines += ["", f"**成功、异步ID与后续步骤**：{after}", "", f"**幂等/并发**：{idem}", "", "**响应合同**："]
            lines += ["", f"完整成功JSON：[响应索引](examples/endpoint-success-examples.json)中键 `{key}`；证据标签说明是否本轮执行/历史真实/仅Schema示例。", ""]
            for status, response in item.get("responses", {}).items():
                if status.startswith("2"):
                    lines += [f"- HTTP{status}：`{json.dumps(response.get('content', {}), ensure_ascii=False)}`"]
            if evidence:
                lines += ["", f"**完整真实响应及历史测试证据**：[原始HTTP记录](../../6-实测产物/phase11_live_20261015_v3/{evidence})（response字段；请求字段可一起核对）。"]
            else:
                lines += ["", "**测试证据/完整示例**：[本轮HTTP探测](contracts/http-probe.json)、[完整探测响应](contracts/http-responses.json)、[Phase11验收](../phase11/ACCEPTANCE.md)。探测逐项标记是否执行；未执行的状态变化只引用既有测试，不冒充真实模型结果。"]
            lines += ["", "**失败处理**：适用本文通用错误表；409先重新GET资源判断状态/版本再决定新请求，禁止自动无限retry。422先修正参数；503保留X-Request-ID给运维。", ""]
    meanings = {"request_id":"客户端逻辑请求标识；HTTP层长度<=64并按principal哈希送入引擎", "idempotency_key":"同一次动作重放键，长度<=128；新动作换键", "source_selector":"指定不可变来源；推荐by_id", "edit":"正文局部修改范围；不是Plan feedback", "remix":"保留与变更约束", "target_episodes":"Create/Remix总集数；Continue新增集数", "plan_fingerprint":"审批快照校验值；传给审批body.fingerprint", "fingerprint":"刚读到的plan_fingerprint，精确64字符", "expected_revision":"客户端已读取的当前修订号，用于并发保护", "expected_version":"当前角色Canon版本号，首次导入0", "selected_character_ids":"角色库标识列表，需配合角色绑定", "role_bindings":"每角色character_id+role_id或story_role_id+performer_id", "character_profile_revisions":"选择调用人Runtime修订", "temporary_profile_overrides":"本次临时Runtime覆盖，不能修改固定Canon", "new_fictional_roles":"临时虚构角色；已有角色优先经库导入", "generated_episode_ids":"本次生成集；完整episodes可含继承集", "playback_position_ms":"来源参考元数据，不实现毫秒截断、续播或保存进度", "continuation_kind":"使用next_episode；resume_playback不是正文生成，异步任务将失败", "characters":"导入批次1..100角色或故事角色选择（依所属模型）", "asset_refs":"资源引用，无自动下载/合成", "task_id":"TaskView中为大纲队列ID；RenderTask中为演绎位置哈希，两个命名空间", "performance":"从Utterance保留的演绎参数；客户端按能力映射", "usage":"累计调用记录；按call_id去重，reasoning可能已含于output", "status":"所属资源状态；详见状态图，不同资源不能混用", "user_instruction":"用户要求，不能为空", "feedback":"未审批大纲的新反馈，不修改已发布正文", "canon":"固定身份与事实，非空既有值不能覆写", "runtime_defaults":"公共默认动态参数，不等于调用人个性化版本", "patch":"仅Runtime字段；省略字段不改，明确null的语义按模型处理", "retain_elements":"逐项保留约束，至少1项", "transform_elements":"逐项变更要求，至少1项", "downstream_policy":"branch_from_here保留后续；replan_affected把受影响后续纳入审批", "target":"按scope解析的集/场景/台词ID，不支持任意播放区间", "audience_band":"仅14-17、18-35、36-55；无低龄儿童档", "target_duration_sec":"每集创作目标，不是实际音频时长保证", "content_form":"当前HTTP只接受audio_drama", "narrator_id":"旁白标识；不冒充角色role_id", "speaker_role_id":"本故事内说话角色ID", "performer_id":"演绎者/音色路由ID，与character_id、role_id分离"}
    lines += ["## 全部输入/输出 Schema 字段", "", "以下表直接展开OpenAPI components：类型、必填、默认、enum/const、上下限、引用与嵌套结构均保留在约束列。`$ref` 指向本节同名模型；数组items、anyOf、oneOf按原Schema解释。未列default的可选字段可能由服务生成或default_factory构造，不代表默认null。输出字段的业务含义依名称及各端点上下文。", "", "额外运行时规则：request_id<=64；idempotency_key<=128；14-17不能mature_non_explicit；must_include/must_avoid不能同项；safety_profile评级须与preferences一致。选中角色绑定完整且唯一，voice.performer_id一致。Revise目标必须存在且范围无歧义，详见[范围表](01-architecture.md)。", ""]
    for name, schema in sorted(spec["components"]["schemas"].items()):
        lines += [f"### {name}", ""]
        if "properties" not in schema:
            lines += ["```json", json.dumps(schema, ensure_ascii=False, indent=2), "```", ""]
            continue
        lines += ["|字段|必填|类型/默认/全部约束|业务含义|", "|---|---|---|---|"]
        for field, value in schema["properties"].items():
            clean = {k:v for k,v in value.items() if k != "title"}
            lines.append(f"|`{field}`|{'是' if field in schema.get('required', []) else '否'}|`{json.dumps(clean, ensure_ascii=False).replace('|', '&#124;')}`|{value.get('description', meanings.get(field, '按字段名和所属模型表达的结构化数据；完整值见真实响应'))}|")
        if "additionalProperties" in schema:
            lines += ["", f"额外字段：`{json.dumps(schema['additionalProperties'])}`；false表示拒绝未声明字段。"]
        lines += [""]
    (OUT/"02-api-reference.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Rendered {count} endpoint descriptions and {len(spec['components']['schemas'])} component schemas")


if __name__ == "__main__":
    main()
