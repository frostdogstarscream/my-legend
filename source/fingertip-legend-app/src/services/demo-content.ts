import type { Chapter, Character, PerformanceMedia, Story } from '../types/domain'
export const demoChapters = (): Chapter[] => [
  { id: 'chapter-1', title: '古城中的来信', source: 'demo', paragraphs: ['黄昏时分，一封没有署名的信送到了古城。墨羽打开信封，里面只有一张画着山川的旧地图。', '“如果你愿意寻找，就来竹桥边。”信的末尾写着这样一句话。灵墨轻轻合上书，决定陪他一起出发。', '这是用于体验阅读与章节切换的示例内容。你保存的灵感仍在作品详情中，接入生成服务后，这里会展示对应作品的真实章节。'] },
  { id: 'chapter-2', title: '雨后的竹林', source: 'demo', paragraphs: ['雨停了，竹叶上的水珠还在轻轻摇晃。墨羽把那封信收进怀里，沿着青石小路向前走。', '“你听见了吗？”灵墨停下脚步。远处传来一声清脆的铃响，像是谁在雾里轻轻拨动了风。', '竹林尽头，一位披着浅色斗篷的旅人正站在桥边。他没有转身，只将一卷旧书放在石栏上。'] },
  { id: 'chapter-3', title: '神秘的来客', source: 'demo', paragraphs: ['“山海的故事，从来不会只写在纸上。”旅人说，“有些故事，要等愿意寻找它的人出现。”', '墨羽伸出手，书页却在风中自己翻开。第一页上，是古城的轮廓；第二页上，画着他和灵墨此刻站立的竹桥。', '而第三页，还是空白。', '灵墨抬起头，望向雾中渐渐清晰的山路。“看来，接下来的故事，要由我们来写了。”'] },
]
export const demoMedia: PerformanceMedia = {
  posterUrl: '/static/performance/stage.png', duration: 34.1,
  actors: [
    { id: 'shenyan', name: '沈砚', portraitUrl: '/static/performance/shenyan.png' },
    { id: 'sutang', name: '苏棠', portraitUrl: '/static/performance/sutang.png' },
    { id: 'laoqin', name: '老秦', portraitUrl: '/static/performance/laoqin.png' },
  ],
  cues: [
    { speakerId: 'shenyan', start: 0, end: 10.8, text: '那封撤销通知，到底是谁留下的？' },
    { speakerId: 'sutang', start: 10.8, end: 22.4, text: '先查清通知的来路，别让站里的人再起争执。' },
    { speakerId: 'laoqin', start: 22.4, end: 34.1, text: '那就再发一遍撤销通知。别在站里猜谁是鬼，先把人救下来。' },
  ],
}
export const demoCharacters: Character[] = [
  { id: 'demo-char-001', name: '墨羽', description: '从古城出发的少年，喜欢追寻新的故事。', avatarUrl: '/static/figma/avatar.png', owned: true, tags: ['勇敢', '好奇', '重情义'], biography: '墨羽总会先问一句“为什么”。他相信每封信背后都有一段值得寻找的故事，也愿意为了伙伴勇敢地踏出第一步。', voiceDescription: '清朗少年 · 温暖明亮' },
  { id: 'demo-char-002', name: '灵墨', description: '安静的故事伙伴，陪你听见更多想象。', avatarUrl: '/static/figma/ai-avatar.png', owned: true, tags: ['温柔', '聪慧', '善倾听'], biography: '灵墨喜欢收集旅途中的声音。她总能留意别人忽略的细节，也愿意陪伙伴慢慢找到答案。', voiceDescription: '温柔少女 · 清晰舒缓' },
]
export function demoSeeds(): Story[] {
  return [
    { id: 'demo-performance-001', title: '雾港第三声', genre: '悬疑推理', idea: '一间临港电台，一封撤销通知。沈砚、苏棠与老秦面对新的疑问，先作出救人的决定。', status: 'ready', progress: 82, chapter: '演绎预览 · 当前版本', coverUrl: demoMedia.posterUrl, createdAt: '2026-10-14T00:00:00.000Z', media: demoMedia, chapters: [{ id: 'wugang-preview', title: '电台里的决定', source: 'demo', paragraphs: ['老秦看向屋内的两人。', '“那就再发一遍撤销通知。别在站里猜谁是鬼，先把人救下来。”', '这里展示截图中的对白片段；完整剧本与视频尚未接入。'] }] },
    { id: 'demo-story-001', title: '山海奇遇录', genre: '国风奇幻', idea: '一封来自山海的信，将墨羽带进了古城之外的世界。他与灵墨一起寻找遗失的书卷。', status: 'ready', progress: 42, chapter: '第 3 章 · 神秘的来客', coverUrl: '/static/figma/home-background.png', createdAt: '2026-10-14T00:00:00.000Z', roleIds: ['demo-char-001', 'demo-char-002'], chapters: demoChapters() },
    { id: 'demo-draft-001', title: '古城来信', genre: '冒险成长', idea: '少年收到了一封没有署名的信。', status: 'draft', progress: 0, chapter: '灵感草稿 · 刚刚保存', coverUrl: '/static/figma/home-background.png', createdAt: '2026-10-14T00:00:00.000Z' },
  ]
}
