# 09 部署与故障排查

建议Python3.11、PostgreSQL16、Docker Compose v2。服务安装声明支持Python>=3.10，本批工程验证为3.11/PG16。API与Worker各一个进程，使用相同数据库/schema/角色配置；模型凭据只需Worker。不要依赖内存保存正式任务。首次部署迁移004，升级前备份。

## Windows 11 PowerShell

```powershell
git clone --branch codex/storygenerate-handoff https://github.com/zblds-a/StoryGenerate.git
Set-Location StoryGenerate
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[service,test]'
# 新数据库：输入将用于首次初始化的密码，不写入文件
$securePassword = Read-Host 'PostgreSQL password' -AsSecureString
$env:POSTGRES_PASSWORD = [Net.NetworkCredential]::new('', $securePassword).Password
docker compose up -d postgres
docker compose ps
$encodedPassword = [Uri]::EscapeDataString($env:POSTGRES_PASSWORD)
$env:STORY_DATABASE_URL = 'postgresql+psycopg://storygen:' + $encodedPassword + '@localhost:5432/storygenerate'
$env:STORY_DATABASE_SCHEMA = 'story_handoff_demo'
$env:STORY_API_KEY = python -c 'import secrets; print(secrets.token_urlsafe(32))'
$auth = @{}
$auth[$env:STORY_API_KEY] = @{principal_id='handoff-demo'; admin=$true}
$env:STORY_API_KEYS = $auth | ConvertTo-Json -Compress
python -m drama_engine.service migrate
python -m drama_engine.service serve --host 127.0.0.1 --port 8000
```

这是本机演示admin身份；生产为各业务后端分配独立普通principal，只给角色管理导入使用admin。API启动后在第二个进程安全注入同样配置；不要把整个环境转存仓库，使用部署系统/终端安全会话注入。设置模型网关后运行：

```powershell
# 从安全环境注入DRAMA_LLM_API_KEY或STORY_LLM_API_KEY，勿写死密钥
$env:DRAMA_LLM_BASE_URL = 'https://your-model-gateway.example/v1'
# .example是占位符，必须换成团队实际可访问且兼容的网关
# 模型名按供应商支持设置；不要照抄其他部署的内部地址
python -m drama_engine.service worker
```

不同终端不会自动继承刚才运行中的进程环境；若第二终端缺少连接/认证配置，Worker会拒绝启动。需要同样的安全环境注入，不能假设启动API就同时启动了Worker。

## Linux

```bash
git clone --branch codex/storygenerate-handoff https://github.com/zblds-a/StoryGenerate.git
cd StoryGenerate
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[service,test]'
read -rsp 'PostgreSQL password: ' POSTGRES_PASSWORD
export POSTGRES_PASSWORD
docker compose up -d postgres
export STORY_DATABASE_URL
STORY_DATABASE_URL=$(python -c 'import os,urllib.parse; print("postgresql+psycopg://storygen:"+urllib.parse.quote(os.environ["POSTGRES_PASSWORD"],safe="")+"@localhost:5432/storygenerate")')
export STORY_DATABASE_SCHEMA=story_handoff_demo
export STORY_API_KEY
STORY_API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
export STORY_API_KEYS
STORY_API_KEYS=$(python -c 'import os,json; print(json.dumps({os.environ["STORY_API_KEY"]:{"principal_id":"handoff-demo","admin":True}}))')
python -m drama_engine.service migrate
python -m drama_engine.service serve --host 127.0.0.1 --port 8000
# 另一个受控进程注入同样数据库/认证和真实模型环境后：
# python -m drama_engine.service worker
```

shell命令不输出连接串或密钥，但进程环境仍需受主机权限保护。生产使用密钥管理器或编排系统，不将环境文件/备份/日志凭据入Git。已有Docker pgdata卷的密码不会随POSTGRES_PASSWORD环境变化而重置；连接现有库需用已有安全凭据或按数据库管理流程改密，不能删除生产卷“修复”。

## 配置与运行

|环境变量|默认/用途|
|---|---|
|STORY_DATABASE_URL|必填；PostgreSQL psycopg SQLAlchemy URL，密码需URL编码|
|STORY_DATABASE_SCHEMA|可选；隔离schema，由migrate创建，使用安全标识符|
|STORY_API_KEYS|必填JSON：凭据→principal_id/admin；API与Worker加载服务配置|
|DRAMA_LLM_API_KEY / STORY_LLM_API_KEY|真实网关凭据；无Mock回退|
|DRAMA_LLM_BASE_URL / STORY_LLM_BASE_URL|现有OpenAI兼容Provider网关；实际部署必须核对可达地址|
|STORY_LLM_FAST_MODEL / BALANCED_MODEL / STRONG_MODEL / LONG_MODEL / STRONG_ALT_MODEL|现有分层路由模型；前缀均为STORY_LLM_，依Provider能力配置|
|DRAMA_LLM_TIMEOUT|Provider请求超时；不是客户端轮询最大时间|
|STORY_GLOBAL_DEADLINE_SEC / STORY_NODE_DEFAULT_TIMEOUT_SEC|引擎整体/节点时限；结合长任务成本设置|
|STORY_WORKER_LEASE_SECONDS|120；任务租约|
|STORY_WORKER_HEARTBEAT_SECONDS|10，必须>0且小于租约一半|
|STORY_WORKER_POLL_SECONDS|1，大于0|
|STORY_EVIDENCE_DIR|可选原始调用存档目录；限制权限，脱敏后才提交|
|STORY_API_URL / STORY_API_KEY|仅本包客户端脚本使用，非服务端认证配置替代|

完整默认值以[settings.py](../../4-引擎代码/drama_engine/core/settings.py)、[配置](../../4-引擎代码/drama_engine/service/config.py)、[Provider](../../4-引擎代码/drama_engine/llm/openai_compat.py)为准。模型名称不得写成固定产品保证：实际模型路由与供应商返回的actual_model需记录。本轮复用历史qwen调用证据，不宣称现在的网关可用性已重测。

命令：`python -m drama_engine.service openapi --output schema.local.json`导出；`migrate`执行迁移；`serve`提供API；`worker`跑队列。迁移只运行一次协调实例，不让所有副本并发迁移。API启动检查alembic_version为004。Docker镜像可`docker build -f Dockerfile.service -t storygenerate-service .`，以`-e STORY_DATABASE_URL -e STORY_API_KEYS -e STORY_DATABASE_SCHEMA`传入环境；Worker另带模型配置。容器数据库主机不能盲用localhost，应在Docker网络可达。

健康检查GET `/health`只验证数据库；`/docs`/`/openapi.json`匿名可读。局域网联调可serve --host 0.0.0.0，但需防火墙限制调用方，并经团队TLS网关提供受控地址，勿在手册写内部敏感地址。公网必须使用TLS与访问控制。没有`/version`接口：运维保存`git rev-parse HEAD`、镜像digest、启动记录；核对部署制品和提交，不能仅从API版本11.0推断代码SHA。

## 备份与恢复

备份整个数据库/schema，包含角色、profile revisions、Plan、Revision、Job、版本和planning queue。维护窗口停止接收写入并协调Worker；模型原始证据目录另行备份。PG连接密码通过PGPASSFILE或安全环境提供，不放命令行：

```sh
# PGHOST/PGPORT/PGUSER/PGDATABASE和PGPASSFILE由运维注入
pg_dump -Fc --file storygenerate.backup
# 先由运维创建一个空的隔离恢复数据库，再指定其PGDATABASE
pg_restore --exit-on-error --dbname "$PGDATABASE" storygenerate.backup
```

Windows可使用PostgreSQL bin下的pg_dump.exe/pg_restore.exe执行相同参数，路径放在受控备份目录；不要提交.backup文件。恢复到隔离库先对行数/全行hash和迁移号，再启动API和Worker。恢复Worker会识别过期租约，记录FAILED WORKER_INTERRUPTED/取消终态，需显式retry；不会自动悄悄再次付费生成。工程备份恢复示例脚本[phase11_database_restore.py](../../tests/acceptance/phase11_database_restore.py)仅对项目Docker验收schema使用；本轮[恢复证据](contracts/database-restore.json)保留源与恢复全行hash，不含连接串。

|现象|排查与处理|
|---|---|
|SERVICE_REQUIRES_POSTGRESQL|缺STORY_DATABASE_URL或错误后端；生产不允许SQLite|
|SERVICE_AUTH_CONFIGURATION_REQUIRED|STORY_API_KEYS空/JSON不合法/principal或admin类型错误|
|SERVICE_MIGRATION_004_REQUIRED|对正确schema先migrate，核对search_path|
|401/403|Bearer是否传入、principal映射、admin权限；勿记录密钥|
|任务长期QUEUED|Worker是否运行、配置是否同库同schema、模型配置可用|
|WORKER_INTERRUPTED|查进程/心跳/租约；阅读未知在途成本，明确决定retry|
|409审批/角色/Runtime冲突|重新读取最新版本；Plan反馈或新准备，不绕过快照|
|FAILED且HTTP200|业务失败状态；查error/usage，保留原失败证据|
|模拟成功但无音频|符合设计，simulate-tts未合成音频|
|pytest临时目录WinError5|指定新的可写--basetemp；不要删除其他任务的临时目录|

回归：注入`TEST_DATABASE_URL`指向可用于创建/清理隔离schema的测试库，执行`python -m pytest -q`；Windows必要时加`--basetemp <新的可写目录>`。测试替身用于工程验证，不是模型内容证据。零费用验收入口见[10](10-acceptance.md)。
