<script setup lang="ts">
import { onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { buildRpaToolEditorLocation } from '@/utils/rpaMcpConvert';

const route = useRoute();
const router = useRouter();

onMounted(() => {
  router.replace(buildRpaToolEditorLocation({
    sessionId: typeof route.query.sessionId === 'string' ? route.query.sessionId : '',
    skillName: typeof route.query.skillName === 'string' ? route.query.skillName : '',
    skillDescription: typeof route.query.skillDescription === 'string' ? route.query.skillDescription : '',
  }));
});
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-[#f5f7fb] px-6 text-sm text-slate-500 dark:bg-[#101115] dark:text-slate-400">
    Redirecting to MCP Tool Editor...
  </div>
</template>
