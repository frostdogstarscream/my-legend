import type { Character, CreateStoryInput, HomeSummary, LoginInput, LoginResult, Product, Story, User } from '../types/domain'
import { demoCharacters } from './demo-content'
import { readStories, saveStory } from './story-repository'
import { readDemoWallet } from './demo-wallet'

const asset = (name: string) => `/static/figma/${name}`
const demoUser: User = { id: 'demo-user', displayName: '风起云涌', avatarUrl: asset('avatar.png'), vipDays: 28, balance: 1250 }
export function readDemoStories(): Story[] { return readStories() }
export const mockGateway = {
  async login(_input: LoginInput): Promise<LoginResult> {
    return { accessToken: 'local-demo-session', expiresIn: 86400, user: { ...demoUser, balance:readDemoWallet().balance } }
  },
  async home(): Promise<HomeSummary> {
    return { user: { ...demoUser, balance:readDemoWallet().balance }, latestStory: readDemoStories()[0] || null }
  },
  async stories(): Promise<Story[]> { return readDemoStories() },
  async createDraft(input: CreateStoryInput): Promise<Story> {
    const story: Story = {
      ...input, id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      status: 'draft', progress: 0, chapter: '创意草稿', coverUrl: asset('home-background.png'), createdAt: new Date().toISOString(),
    }
    return saveStory(story)
  },
  async characters(): Promise<Character[]> {
    return demoCharacters
  },
  async products(): Promise<Product[]> {
    return [{ id: 'demo-sku-001', name: '山海装扮', description: '墨羽专属故事装扮', imageUrl: asset('shop.png'), price: 300, currency: 'coins' }]
  },
}
