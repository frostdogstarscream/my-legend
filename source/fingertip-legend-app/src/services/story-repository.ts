import type { GenerationTask, Story } from '../types/domain'
import { demoChapters, demoSeeds } from './demo-content'
const STORY_KEY = 'zj-shell:demo-stories:v1'
const TASK_KEY = 'my-legend:tasks:v1'
type StoredTask = GenerationTask & { startedAt: number; failDemo?: boolean }
export function readStories(): Story[] {
  const saved = uni.getStorageSync(STORY_KEY)
  const valid: Story[] = Array.isArray(saved) ? saved.filter(s => s && typeof s.id === 'string' && typeof s.title === 'string') : []
  const seeds = demoSeeds()
  return [...valid.map(s => ({ ...seeds.find(seed => seed.id === s.id), ...s })), ...seeds.filter(s => !valid.some(v => v.id === s.id))]
}
export function saveStory(story: Story) { const others = readStories().filter(s => s.id !== story.id); uni.setStorageSync(STORY_KEY, [story, ...others]); return story }
export function getStory(id: string) { const story = readStories().find(s => s.id === id); if (!story) throw new Error('没有找到这份故事'); return story }
function readTasks(): StoredTask[] { const saved = uni.getStorageSync(TASK_KEY); return Array.isArray(saved) ? saved : [] }
export function startDemoTask(storyId: string, failDemo = false): GenerationTask {
  const story = getStory(storyId)
  const existing = readTasks().find(t => t.storyId === storyId && t.status === 'running')
  if (existing) return existing
  const task: StoredTask = { id: `task-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, storyId, startedAt: Date.now(), status: 'running', progress: 0, message: '正在整理灵感', failDemo }
  uni.setStorageSync(TASK_KEY, [task, ...readTasks()]); saveStory({ ...story, status: 'generating' }); return task
}
export function getDemoTask(id: string): GenerationTask {
  const tasks = readTasks(), task = tasks.find(t => t.id === id)
  if (!task) throw new Error('没有找到这次创作记录，请返回故事列表')
  if (task.status !== 'running') return task
  const progress = Math.min(100, Math.floor((Date.now() - task.startedAt) / 80))
  task.progress = progress
  task.message = progress < 35 ? '构思故事与人物关系' : progress < 85 ? '整理示例章节与对白' : '准备阅读预览'
  if (progress >= 100) {
    task.status = task.failDemo ? 'failed' : 'succeeded'; task.message = task.failDemo ? '这次创作没有完成，灵感草稿已保留' : '故事预览已准备好'
    const story = getStory(task.storyId)
    saveStory({ ...story, status: task.failDemo ? 'failed' : 'ready', chapters: task.failDemo ? story.chapters : demoChapters(), chapter: task.failDemo ? '草稿已保留' : '第 1 章 · 示例章节' })
  }
  uni.setStorageSync(TASK_KEY, tasks); return task
}
