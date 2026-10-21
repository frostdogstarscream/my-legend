<script setup lang="ts">
import { onLaunch } from '@dcloudio/uni-app'
import { hydrateSession } from './stores/session'
onLaunch(() => hydrateSession())
</script>

<style>
@import './styles/theme.css';
</style>
