<script setup lang="ts">
import { ref,computed } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import CharacterPortrait from '../../components/CharacterPortrait.vue'
import FeatureLayout from '../../components/FeatureLayout.vue'
import { api } from '../../services/api'
import type { Character } from '../../types/domain'
import { errorMessage,goPage,goTab,requireSession,showMessage } from '../../utils/navigation'
import {readSelection,toggleSelection} from '../../stores/creation-selection'
const selected=ref<string[]>([])
const picked=computed(()=>selected.value.map(id=>roles.value.find(r=>r.id===id)).filter((r):r is Character=>!!r))
const roles=ref<Character[]>([]),error=ref(''),loading=ref(true)
const search=ref('')
const filteredRoles=computed(()=>roles.value.filter(r=>(r.name+' '+r.tags?.join(' ')).includes(search.value.trim())))
function choose(role:Character){if(!toggleSelection(role.id))showMessage('最多选择六名故事角色');selected.value=readSelection()}
async function load(){if(!requireSession())return;selected.value=readSelection();loading.value=true;error.value='';try{roles.value=await api.characters()}catch(e){error.value=errorMessage(e)}finally{loading.value=false}}
onShow(load)
</script>
<template><FeatureLayout title="我的角色" subtitle="故事里的伙伴，也在等你的下一次出发" active="characters" @reconnect="load"><view class="page-content character-content"><view class="row-between"><text class="section-title serif">挑选故事角色</text><text class="small-note">{{roles.length}} 位</text></view><view v-if="loading" class="state-panel">正在寻找伙伴…</view><view v-else-if="error" class="state-panel"><text class="error-copy">{{error}}</text><button class="warm-button" @tap="load">重试</button></view><view v-else><input v-model="search" class="form-input" aria-label="搜索角色" placeholder="搜索角色或服装"/><text class="small-note spaced">喜欢谁，就带谁走进故事。</text><view class="character-list"><view v-for="role in filteredRoles" :key="role.id" class="paper-panel role-item" :class="{selected:selected.includes(role.id)}" role="button" :aria-label="role.name" :aria-pressed="selected.includes(role.id)" tabindex="0" @tap="choose(role)" @keydown.enter="choose(role)"><CharacterPortrait :role="role" class="role-avatar" mode="aspectFill"/><view class="role-copy"><text class="role-name serif">{{role.name}}</text><text class="role-tags">{{role.tags?.join(' · ')}}</text><button class="role-detail-button" @tap.stop="goPage('role-detail',{id:role.id})">查看角色 ›</button><text class="selection-state">{{selected.includes(role.id)?'已选中 ✓':'点击选择'}}</text></view></view></view></view></view><view class="selection-bar"><view class="selection-summary"><view class="picked-avatars"><CharacterPortrait v-for="role in picked" :key="role.id" :role="role" mode="aspectFill"/></view><text>已选 {{selected.length}} / 6 位</text></view><button class="warm-button" :disabled="!selected.length" @tap="goTab('create')">用这些角色创作</button></view></FeatureLayout></template>
<style scoped>

.character-content{padding-bottom:calc(var(--tabbar-height) + 150px)}
.selection-bar{position:fixed;z-index:19;bottom:calc(var(--tabbar-height) + 16px + var(--bottom-inset));left:0;right:0;max-width:var(--app-width);margin:auto;padding:10px 16px;background:#fff6e8;box-shadow:0 -4px 16px #65432115;box-sizing:border-box}.selection-summary{display:flex;align-items:center;justify-content:space-between;gap:6px;font-size:12px;margin-bottom:8px}.picked-avatars{display:flex;gap:4px}.picked-avatars :deep(.character-portrait){width:30px;height:30px;border-radius:50%}.selection-bar button{width:100%;min-height:44px;font-size:14px}.role-item.selected{border-color:#a8452d;box-shadow:0 0 0 1px #a8452d}.selection-state{display:block;font-size:13px;padding:10px 0;color:#9e3425}
.character-list { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 12px; margin-top: 14px; }
.role-item { min-width: 0; padding: 12px; background: #fbf7ff; border-color: #e5d9f0; }
.role-avatar { width: 100%; height: 118px; border-radius: 14px; background: #f1e5ff; }
.role-copy { min-width: 0; text-align: center; }
.role-name { display: block; font-size: 16px; line-height: 1.4; min-height: 45px; padding-top: 9px; color: #352360; overflow-wrap: anywhere; }
.role-tags { display: block; color: #7955a4; font-size: 12px; margin: 4px 0; }
.role-detail-button { width: 100%; min-height: 44px; color: #7955a4; font-size: 13px; }
.role-copy .warm-button { width: 100%; min-height: 44px; padding: 10px 4px; font-size: 13px; }
@media (max-width: 359px) { .character-list { gap: 10px; } .role-item { padding: 10px; } .role-name { font-size: 14px; } }

</style>
