# 四操作 HTTP 示例

先按 `docs/phase11/DEPLOYMENT.md` 启动 API/Worker，使用 admin 密钥向 `POST /v1/characters/import` 提交 `characters.json`，将调用密钥安全注入 `STORY_CLIENT_API_KEY`。示例绑定 character_id → role_id → performer_id，与导入样例一致。

以下客户端使用真实 HTTP；模型在服务 Worker 中执行。它自动准备大纲、等待、可选修订、审批、等待正文并导出，失败即报错，不自动重复收费调用。审批行为由运行此命令显式授权；用于示例和验收。正式 App 应先显示 Plan，用户确认后再调用 approve。

```sh
python examples/phase11/http_client.py --request examples/phase11/create.json --feedback '保留固定事实，增强共同核对设备的行动' --output service-private/create
python examples/phase11/http_client.py --request examples/phase11/revise.json --source-version <Create返回的版本ID> --output service-private/revise
python examples/phase11/http_client.py --request examples/phase11/continue.json --source-version <Revise返回的版本ID> --output service-private/continue
python examples/phase11/http_client.py --request examples/phase11/remix.json --source-version <Continue返回的版本ID> --output service-private/remix
```

客户端为每次独立操作生成请求ID和幂等键并保存 exact request.json；从运行中断恢复时先查询已打印的 task/job ID，不要直接重新运行以免创建新任务。JSON 文件可以直接作为四操作 API 请求模板，替换所有尖括号字段；Revise 示例客户端会自动定位来源第一集的第一个对白场景。Plan 修订采用 expected_revision；审批采用 expected_revision + plan_fingerprint；字段均从服务器返回值读取，不能自行猜测。

真实测试请求、审批、版本和错误响应也保存在 Phase11 实测目录。`simulate-tts` 的结果仅为契约验证，不是实际音频合成。
