<script setup lang="ts">
import { ref } from 'vue'
import { onLoad,onShow } from '@dcloudio/uni-app'
import NativeActorPreview from '../../components/NativeActorPreview.vue'
import CharacterPortrait from '../../components/CharacterPortrait.vue'
import FeatureLayout from '../../components/FeatureLayout.vue'
import type { Character } from '../../types/domain'
import { api } from '../../services/api'
import { errorMessage,goTab,requireSession,showMessage } from '../../utils/navigation'
import {readSelection,toggleSelection} from '../../stores/creation-selection'
const selected=ref<string[]>([])
onShow(()=>{selected.value=readSelection()})
const role=ref<Character|null>(null),error=ref('')
onLoad(async q=>{if(!requireSession())return;try{role.value=(await api.characters()).find(c=>c.id===q?.id)||null;if(!role.value)error.value='没有找到这个伙伴'}catch(e){error.value=errorMessage(e)}})
function useRole(){if(!role.value)return;if(!toggleSelection(role.value.id))showMessage('最多选择六名故事角色');selected.value=readSelection()}
</script>
<template><FeatureLayout title="角色详情" subtitle="你的故事伙伴"><view class="page-content"><text v-if="error" class="error-copy">{{error}}</text><template v-if="role"><view class="role-portrait-panel"><CharacterPortrait :role="role" class="role-portrait" mode="aspectFit"/><text class="role-name serif">{{role.name}}</text><view class="chip-list role-tags"><text v-for="tag in role.tags" :key="tag" class="pill violet">{{tag}}</text></view><text class="copy">{{role.description}}</text></view><view class="paper-panel section-gap"><text class="section-title serif">伙伴档案</text><text class="copy">{{role.biography}}</text><text class="field-label">适合题材</text><text class="small-note">{{role.tags?.includes('国风')?'国风奇幻 · 冒险成长':'生活治愈 · 悬疑故事'}}</text></view><NativeActorPreview v-if="role.stageBinding" :key="role.id" :binding="role.stageBinding"/><button class="warm-button full-button" @tap="useRole">{{selected.includes(role.id)?'取消选择':'选中这个角色'}}</button><button class="outline-button full-button" @tap="goTab('characters')">返回角色选择（{{selected.length}} 位）</button></template></view></FeatureLayout></template>
<style scoped>.role-portrait-panel{text-align:center;padding:12rpx 20rpx 24rpx}.role-portrait{width:250rpx;height:280rpx;margin:0 auto;border-radius:32rpx;background:#f3ecff}.role-name{display:block;font-size:56rpx;margin:26rpx 0}.role-tags{justify-content:center;margin-bottom:26rpx}</style>
