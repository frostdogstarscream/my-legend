<script setup lang="ts">
import {ref} from 'vue'
import {onShow} from '@dcloudio/uni-app'
import FeatureLayout from '../../components/FeatureLayout.vue'
import AppSheet from '../../components/AppSheet.vue'
import {api} from '../../services/api'
import {clearSession,sessionState} from '../../stores/session'
import type {User} from '../../types/domain'
import {errorMessage,requireSession,goTab} from '../../utils/navigation'
const user=ref<User|null>(sessionState.session?.user||null),error=ref(''),about=ref(false)
onShow(async()=>{if(!requireSession())return;try{user.value=(await api.home()).user;error.value=''}catch(e){error.value=errorMessage(e)}})
async function logout(){try{await api.logout()}finally{clearSession();uni.reLaunch({url:'/pages/login/index'})}}
</script>
<template><FeatureLayout title="我的" subtitle="每一个故事，都有你的影子" active="profile"><view class="page-content"><view class="paper-panel profile-panel"><image :src="user?.avatarUrl||'/static/figma/avatar.png'" class="profile-avatar" mode="aspectFill"/><view><text class="profile-name serif">{{user?.displayName||'故事创作者'}}</text><text class="small-note">欢迎回到你的故事世界</text></view></view><text v-if="error" class="error-copy">{{error}}</text><view class="paper-panel profile-links section-gap"><button @tap="goTab('stories')">我的故事<text>›</text></button><button @tap="goTab('characters')">故事角色<text>›</text></button><button @tap="about=true">关于我的传说<text>›</text></button></view><button class="outline-button full-button" @tap="logout">退出登录</button></view><AppSheet title="我的传说" :open="about" @close="about=false"><text class="section-title serif">故事，从你的一个念头开始</text><text class="copy spaced">选几个喜欢的角色，写下想讲的故事。你可以读它、听它，也可以看角色把它演出来。</text><text class="copy spaced">想换个结尾，或让故事继续？随时回来接着写。</text></AppSheet></FeatureLayout></template>
<style scoped>.profile-panel{display:flex;align-items:center;gap:26rpx}.profile-avatar{width:116rpx;height:116rpx;border-radius:50%;border:4rpx solid #e5bd66}.profile-name{display:block;font-size:42rpx;margin-bottom:12rpx}.profile-links{padding:0 28rpx}.profile-links button{display:flex;justify-content:space-between;width:100%;padding:28rpx 0;border-bottom:2rpx solid #eadcc9}.profile-links button:last-child{border:0}</style>
