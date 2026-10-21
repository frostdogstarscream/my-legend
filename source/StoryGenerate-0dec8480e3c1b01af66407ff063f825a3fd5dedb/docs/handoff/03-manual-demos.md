# 03 八套人工手动演示

以下步骤从仓库根执行，已按当前Schema复核。森林故事是**待联调执行的演示输入**，不是归档真实生成结果；真实执行链见演示8。当前最低年龄档14-17，不能把“适合儿童”的创意要求当作低龄验收通过。独立新同事真人走查尚待接手团队签收。

## 通用准备与三种客户端

先按[09](09-deployment.md)启动数据库/API/Worker，安全设置`STORY_API_URL`与`STORY_API_KEY`。导入需要admin身份；生产业务调用应换普通身份。每个新动作使用新UUID；网络超时重放**已保存的同一文件**，不要重新生成幂等键。生成/反馈会调用模型收费，脚本不自动审批、不自动重试。

Python客户端只用标准库，可在Windows/Linux运行：

```sh
python tools/handoff/client.py send GET /health
python tools/handoff/client.py send GET /v1/characters --output characters.local.json
python tools/handoff/client.py send POST /v1/characters/import --body docs/handoff/examples/forest.characters.json
```

PowerShell等价调用（UTF-8字节请求，兼容中文；PowerShell7推荐）：

```powershell
$env:STORY_API_URL = 'http://127.0.0.1:8000'
# STORY_API_KEY由安全环境提供；不粘贴进仓库脚本
& ./docs/handoff/examples/call.ps1 -Method GET -Path /health
& ./docs/handoff/examples/call.ps1 -Method POST -Path /v1/plans -BodyFile create.local.json -OutputFile submitted.local.json
```

curl等价调用：Linux `bash docs/handoff/examples/call.sh POST /v1/plans create.local.json`。Windows使用`curl.exe`而不是旧PowerShell的curl别名：

```powershell
curl.exe --fail-with-body --max-time 30 -H "Authorization: Bearer $env:STORY_API_KEY" -H 'Content-Type: application/json' --data-binary '@create.local.json' "$env:STORY_API_URL/v1/plans"
```

每套演示共享以下**显式审批流程**。将ID替换为实际响应值，尖括号是说明占位符，不可原样发送：

```sh
python tools/handoff/client.py send POST /v1/plans --body operation.local.json --output submitted.local.json
python tools/handoff/client.py poll tasks <task_id> --output task.local.json
python tools/handoff/client.py send GET /v1/plans/<task.result.plan_id> --output plan.local.json
# 人工审阅大纲、角色、修改范围；未确认不要执行approve
```

审批文件必须从刚读取的Plan生成；以下PowerShell以实际revision/fingerprint填写，无硬编码0：

```powershell
$plan = Get-Content plan.local.json -Raw -Encoding UTF8 | ConvertFrom-Json
$approval = @{expected_revision=$plan.plan_revision; fingerprint=$plan.plan_fingerprint; idempotency_key=[guid]::NewGuid().ToString('N')}
$approval | ConvertTo-Json | Set-Content approval.local.json -Encoding utf8
# 确认上面Plan后再调用；审批直接入队正式生成
python tools/handoff/client.py send POST "/v1/plans/$($plan.plan_id)/approve" --body approval.local.json --output job.local.json
$job = Get-Content job.local.json -Raw -Encoding UTF8 | ConvertFrom-Json
python tools/handoff/client.py poll jobs $job.job_id --output completed.local.json
$done = Get-Content completed.local.json -Raw -Encoding UTF8 | ConvertFrom-Json
python tools/handoff/client.py send GET "/v1/story-versions/$($done.result_story_version_id)" --output delivery.local.json
python tools/handoff/client.py send GET "/v1/story-versions/$($done.result_story_version_id)/export?format=script" --output story.local.txt
python tools/handoff/client.py send GET "/v1/story-versions/$($done.result_story_version_id)/render-manifest" --output render.local.json
```

Linux/Python生成审批文件的等价方式（读真实Plan，不自动发审批）：

```sh
python -c 'import json,uuid; p=json.load(open("plan.local.json",encoding="utf-8")); json.dump(dict(expected_revision=p["plan_revision"],fingerprint=p["plan_fingerprint"],idempotency_key=uuid.uuid4().hex),open("approval.local.json","w",encoding="utf-8"))'
```

轮询默认3秒、最长1800秒，可`--seconds 3600`。超时仅停止客户端等待，保留ID继续查，不能新建任务代替重连。终态FAILED/CANCELLED脚本退出2，不将其正文用于下一环。所有演示统一排查：401凭据、403admin、404来源/租户、409版本/幂等、422输入；长时间QUEUED查Worker，FAILED查error和usage，不无限retry。

## 演示1：从零创建森林故事

目标：导入小熊/小兔、生成两集120秒创作目标的大纲，经审批得到正文与逐句标注。

1. admin导入[forest.characters.json](examples/forest.characters.json)；200返回两个角色version1。若已存在，不重复导入expected_version0，先GET角色检查并沿用；不要为了演示覆写Canon。
2. 复制[forest.create.json](examples/forest.create.json)为`operation.local.json`，更换request_id和idempotency_key两个`REPLACE-UUID`。真实输入是“森林找宝箱、幽默、反转”；年龄档用当前支持的14-17，低龄需求另行评估。
3. 运行通用POST→poll Task→GET Plan。检查角色bear/rabbit各有performer，集数2，角色身份未漂移。
4. 审批当前Plan，poll Job到SUCCEEDED；GET版本、script、render。
5. 成功标准：READY完整两集，台词都有ID/有效说话人/演绎标注，render顺序连续、发声任务performer有效。实际笑点/反转需读全文判断；120秒不是音频时长承诺。

排查：角色不存在先导入；voice.performer_id与role_bindings不一致需修正；Worker模型失败保留证据，不把测试替身替换成成功。此新题材本轮未付费生成，真实样本见[05](05-story-gallery.md)。

## 演示2：未审批时改大纲

目标与输入：“不要得到金银宝藏，最后发现宝箱里是他们小时候的回忆。”前提为演示1的Plan仍AWAITING_APPROVAL，尚未approve。

1. 读取最新Plan，记下revision。复制[plan.revision.json](examples/plan.revision.json)到`feedback.local.json`，用实际expected_revision和新key。
2. `python tools/handoff/client.py send POST /v1/plans/<plan_id>/revisions --body feedback.local.json --output feedback-task.local.json`。
3. 202返回新的task_id；`poll tasks <反馈task_id>`，然后GET同一plan_id与`/revisions`。
4. 对照两个outline，确认回忆结局要求、revision+1、fingerprint变化；从新Plan重建approval文件并人工确认审批。
5. 正文成功后核对Job.approved_plan_revision为新revision，正文确实兑现新结局。

409 STALE_PLAN时重新读取，不能强行提交旧revision。APPROVED后不能在同Plan上反馈；需新准备请求。历史真实链的此步骤见演示8 create/revision_task与revised_plan。

## 演示3：已生成后修改一个场景，再改一条台词

目标：“第二集最后一场不要让小熊哭，改成一起庆祝。”前提：下载演示1完整`delivery.local.json`，确定第二集最后场景的**数组位置和实际ID**。场景不是固定三场，先展开JSON查数目。

```sh
# 将3替换成第二集实际最后场景位置
python tools/handoff/make_request.py revise delivery.local.json "第二集最后一场不要让小熊哭，改成小熊和小兔一起庆祝；其他内容不变" --episode 2 --scene 3 --scope scene --output operation.local.json
```

此工具只生成请求并校验Schema，不调用模型。检查edit.target中的实际episode_id/scene_id及`branch_from_here`，再执行通用审批流程。新旧story_id相同、story_version_id不同，parent为所选来源。逐个比较非目标utterance完整JSON（包括ID、emotion/语气等），应完全一致；不是只比正文文本。目标场景对照是否庆祝、人物与事实一致。

台词级第二次修改基于**刚成功的修订版本**再GET到`revised.local.json`。定位具体dialogue，例如第1集第1场第1条可能是sfx，不要凭位置猜：

```sh
# 位置是utterances数组位置，实际选择要先读JSON
python tools/handoff/make_request.py revise revised.local.json "把选中这句改得更口语化，保持原意和所有事实；其他内容不变" --scope line --episode 1 --scene 1 --line 2 --output operation.local.json
```

再走prepare→approve→job。仅目标行可变，其余严格一致。来源不存在/越界会拒绝，不能用模糊“第二集结尾”替代target。真实前后例为[S05](samples/S05.md)→[S06](samples/S06.md)；不是本轮森林模型结果。

## 演示4：续写，再次续写

目标：“宝箱中的地图指向天空之城。”前提：已有成功版本和未解线索，不能把失败候选作来源。

```sh
python tools/handoff/make_request.py continue delivery.local.json "接着已完成故事，宝箱中的地图指向天空之城；承接当前关系和事实，不重复已完成情节" --episodes 1 --output operation.local.json
```

依通用流程审批生成。原2集+新增1集=完整3集；`generated_episode_ids`只标新增集。检查旧两集完整JSON未变、人物身份/地图事实不重置、全局序号为3、伏笔更新。

下载成功3集版本为`continued.local.json`，用它再次`make_request.py continue ... --episodes 1`；应完整4集，parent指向3集版本。`target_episodes`不是最终总数。`resume_playback`不能用来续写：该值的请求可入队，但Worker会报告不是生成操作。真实再次续写见[S07](samples/S07.md)，其来源为Phase10原链第3版本，非S06。

## 演示5：平行二创海底故事

目标：“保留小熊小兔，把森林冒险改成海底科幻，加入意外反派。”源版本先下载为`delivery.local.json`。

```sh
python tools/handoff/make_request.py remix delivery.local.json "保留小熊和小兔的固定身份与友谊主题；改为海底科幻冒险，加入意想不到的反派并改变结局" --episodes 2 --retain "小熊小兔的固定身份" --retain "友谊主题" --transform "森林改为海底科幻世界" --transform "加入意外反派和新的结局" --output operation.local.json
```

prepare/审阅/approve/查询。比较保留清单逐项、背景/冲突/结局逐项；不能只检查题目出现“海底”。应新story_id、version_no1、parent准确指原版本，原作仍可GET。若Canon锁定world_id为森林，变更世界可能与固定事实冲突，应改需求或使用兼容角色版本，不能绕过Canon。真实平行版本对照为S03→S04，海底题材本轮没有模型验收证据。

## 演示6：听到一半改变后续

输入：“不去城堡了，改找失踪朋友。”完整四种时机与请求见[04专章](04-direction-change.md)。先确定来源版本及已听位置，客户端记录位置；本服务没有播放事件写入API。常见安全方案为当前场景结束后，从第二集未播放的后缀场景做ending Revise并审阅后续重规划范围。不能保证场景内已听的前半段不变；不能把Continue误用为替换已缓存的第三/四集。

成功标准是新版本的**批准范围**正确、已播放场景保持、下游主动切换新版本；毫秒切换/音频拼接与下游切换通知由客户端团队实现，本轮未实现。遇到最后场景中途且无安全边界时明确提示用户等待场景结束，不能伪称精准续播已通过。

## 演示7：失败、取消、中断恢复与幂等

1. POST一个新Plan保存响应，立即以相同文件POST；应同task_id。改user_instruction但保持key，409 IDEMPOTENCY_KEY_CONFLICT。换key但重复request_id可能REQUEST_ID_CONFLICT。
2. `POST /v1/tasks/<id>/cancel`无body；排队CANCELLED，运行CANCEL_REQUESTED后轮询。取消后的retry应409，不作为可恢复失败。
3. 对真实FAILED的task/job查看error和usage。明确同意再次计费后，分别POST `/v1/tasks/<id>/retry`或`/v1/jobs/<id>/retry`，202同ID；不发body，不在脚本中自动循环retry。原失败保留在attempt_history。
4. API重启后用原ID GET，数据库记录仍在。仅在隔离验收环境强制终止Worker，等待租约过期并重启Worker；任务变FAILED且WORKER_INTERRUPTED（取消中的为CANCELLED），**显式**retry恢复。不要在共享生产环境随意杀Worker做演示。
5. 用旧expected_revision或错误fingerprint审批，应409；当前没有自动“审批过期”时钟/公开过期接口，不要声称EXPIRED可演示。角色版本变化也可能使审批失效，须重新准备/审阅。
6. 执行中的模型请求不能保证立即中断，未知在途用量不得记零。观察数据库恢复与错误，并不代表网络供应商已撤销收费。

本轮真实无模型探测覆盖排队取消、幂等冲突、已成功任务拒绝重试/取消、Plan已批准拒绝反馈及新Session读取；运行中断/正向失败重试的工程证据见Phase11测试，不谎称本轮新做付费模型中断。

## 演示8：真实归档四操作链（可零费用查阅）

这是老街广播站真实HTTP链，非森林示例。来源[phase11 v3](../../6-实测产物/phase11_live_20261015_v3)保留前两轮失败与修复后恢复结果。原始request中的request_id/key与所有响应均可点开核对；不要在新环境原样重放旧来源ID。

|阶段|大纲task_id|plan_id / revision|正式job_id|成功版本|
|---|---|---|---|---|
|Create|e4a7bb5b13ac42dcb8f59dda35e9c3da|e0f7df910be148eab9946b311afe9ac6 / 2|3fddba14a0e44ecd8cbaa48b7261bd5b|8e0711e8c2fe4407b2e1e7449ecee292|
|Revise|570bc7eaccc7473280dbc1c7c73600f3|db2f47617b5d4f2c92cfa49c7fb3d1b3 / 1|b8d09a1f2fae4c708fc082b2f83022be|f3b9178f174f4cf39a7bfa8bd616abcb|
|Continue|d310c389def54b2684ee5d938d653a57|c73cc8db6c474a8e8a6f8e697f50be83 / 1|8656080dfda94702957cd807a9a173ef|1cf88eb73f914da89b058679166830b6|
|Remix|66fc4e5236004ba1a5ca7c229ca74f83|5d76f6f05a784fdea033336a41fda82f / 1|b0071d0a88c743aba1300f7f40c983a6|00311c8545574b6498afbe291e96ffce|

Create反馈task `670b3218304c4c48aa70f48cfb9bcec7` SUCCEEDED，从revision1变2；[反馈请求](../../6-实测产物/phase11_live_20261015_v3/create/revision_submit.json)→[新大纲](../../6-实测产物/phase11_live_20261015_v3/create/revised_plan.json)→[审批QUEUED](../../6-实测产物/phase11_live_20261015_v3/create/approval.json)→[失败后retry](../../6-实测产物/phase11_live_20261015_v3/create/retry.json)→[成功SUCCEEDED](../../6-实测产物/phase11_live_20261015_v3/create/generation_attempt2.json)。不能省略失败而称首轮全成功。

Create→Revise→Continue同story_id `8b8dd2f1fde64ba4a4820432f2ab06c7`，version_no1→2→3，集数2→2→4。Remix新story_id `969862f3639242c48439db74505d544c`，v1、两集，parent为Continue版本。完整四请求见[examples](examples/create.real.json)及API手册。

有该验收schema及相同principal部署权限时，可通过GET以上版本、`/export?format=json`、`/render-manifest`复核；没有旧数据库也可直接打开[S01–S04全文](05-story-gallery.md)和原始JSON。新业务环境必须重新准备新的来源和任务；零费用档案查看不是重新真实模型验收。
