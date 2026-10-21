# 我的传说 · uni-app 前端原型

2026-10-19声画更新：已接入音色选择、后台真实制作、实测事件字幕、102.32秒MP4播放和绑定版本/哈希的审核入口。当前声音与视觉仍待人工确认。见[声画接入与验收](../../ops/my-legend/声画制作接入与验收_20261019.md)，从故事详情进入“观看演绎”或“查看制作与审核”。

## 2026-10-19：真实故事服务接入

本机已增加独立 StoryGenerate API/Worker、数据库和业务适配接口。使用 [完整启动与操作说明](../../ops/my-legend/故事服务接入说明_20261019.md) 中的 `Start-Prototype.ps1`，然后进入“本机创作”。新流程为生成大纲、查看/修改/确认、生成正文、章节阅读，保留失败重试和任务恢复。当前 `.env.local` 已设置真实本机模式，模型密钥只在后台。下文0.2.0说明保留为此前演示基线，其中默认Mock和固定章节不代表本次真实模式。

版本 0.2.0，2026-10-15。使用 Vue 3、TypeScript、uni-app 与 Vite，基于已确认的国风原型扩展。产品显示名、浏览器标题、应用清单、品牌组件均改为“我的传说”。工程实际目录沿用 `D:/Code/AIToy/source/fingertip-legend-app`，保留旧草稿与会话存储键；旧版源文件快照及历史交付包保留。

## 启动

```powershell
npm ci
npm run dev:h5
```

访问 http://127.0.0.1:5193/ ，登录页点击“先体验一下”。默认使用本地示例，无需真实账号或后台。服务只监听本机地址。

## 当前页面与交互

共 12 个页面：登录、首页、创作、故事、角色、我的、角色商城、故事详情、生成进度、阅读、角色详情、视频演绎。底栏仍为“首页 / 创作 / 故事 / 角色 / 我的”。

- 创作：名称、灵感、题材与参与角色；自动保留未提交表单，保存草稿或开始生成演示。
- 故事：搜索、草稿/已完成筛选、空结果、详情与阅读入口；旧版草稿保留。
- 生成：本地任务进度、完成/失败、重试；离开后按实际已用时间恢复。完成后提供带“示例章节”标识的固定内容，不调用 AI，也不声称这些章节由输入生成。
- 阅读：章节切换、目录、三档字号；按作品保存阅读章节与字号。没有真实伴读音频，不输出声音。
- 角色：档案、性格、声音说明、带入创作。
- 演绎：从“故事 → 雾港第三声 → 观看演绎”进入；角色/对白/头像联动、前后句、进度、播放状态、0.8/1/1.25 倍速、字幕与自动切句、横屏预览。
- 我的：会员权益预览、订单空态、本地偏好、退出登录。
- 商城：伙伴和装扮展示、兑换确认；体验兑换只改变本机演示余额，重复确认同一商品不会再次扣减，没有支付请求。

## 媒体的真实边界

场景和透明人物沿用上一版按用户截图重建的素材。沈砚/苏棠对白与分段时间为示例，老秦保留原图句子。没有收到真实 MP4 或配音，所以当前播放器显示静态场景，开始播放只推进示例时间轴。

演绎组件已经保留真实 `video` 分支：作品数据提供 `media.videoUrl` 后使用 uni-app video，并以 `timeupdate` 驱动角色和字幕；播放、暂停、seek、倍速与全屏通过 VideoContext 适配。真实媒体分支尚未使用实际视频验收。参考官方文档：[video](https://uniapp.dcloud.io/component/video.html)、[VideoContext](https://zh.uniapp.dcloud.io/api/media/video-context)。

## 构建与终端边界

```powershell
npm run type-check
npm test
npm run check:assets
npm run build:h5
npm run build:app
npm run build:mp-weixin
```

- H5 输出：`dist/build/h5`，hash 路由。
- App 输出：`dist/build/app`，是可交给 HBuilderX 的应用资源，不是签名 APK/IPA。
- 微信输出：`dist/build/mp-weixin`。本轮仅检查编译，未在微信开发者工具或真机运行。当前插画打包资源约 10 MB，发布前还需处理包体、图片/媒体资源外置或分包，并配置自己的小程序 AppID 和合法域名。

DCloud AppID 与微信 AppID 均留空，未绑定别人的账号。App 仅声明联网权限，没有接入录音、支付或推送。H5/APP 编译时字体静态路径警告由 uni-app 保留到运行时解析；资源检查和浏览器加载另行验证。CLI 与发行说明：[官方 CLI 文档](https://en.uniapp.dcloud.io/worktile/CLI.html)。

## 后台接入位置

默认 `VITE_USE_MOCK=true`。接入后台时复制 `.env.example` 为 `.env.local`，设置 `VITE_USE_MOCK=false` 和 `VITE_API_BASE_URL`，重启或重新构建。真实模式报错时不会回退到演示数据。

- `src/services/api.ts`：统一业务契约和端点适配。
- `src/services/http.ts`：鉴权、HTTP/业务错误和 401 处理。
- `src/services/story-repository.ts`：仅用于本地体验的草稿与生成任务。
- `src/services/demo-content.ts`：固定章节、角色、演绎时间轴。
- `src/services/demo-wallet.ts`：仅用于本地体验的兑换状态。
- `src/types/domain.ts`：Story、Chapter、GenerationTask、PerformanceMedia、角色等类型。

现有服务端还未实现本原型契约。建议依次接账号与作品、异步生成、章节、音频/视频、权益与订单；完整说明见 `docs/后续功能接入说明.md`。模型密钥与支付凭据放在后台，工程中不保存密码。

## 验证资料

类型检查、28 项状态/接口测试和三端资源编译结果及浏览器点验范围见 `docs/验收记录.md`。没有完成真实后台、AI、配音/视频、微信/Android/iOS 真机或签名安装包验收。源码和编译资源随交付包提供，测试截图见 `docs/evidence`。
