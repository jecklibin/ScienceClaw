<template>
  <div class="flex flex-col h-full w-full overflow-hidden tools-page">
    <!-- Hero Header -->
    <div class="flex-shrink-0 relative overflow-hidden">
      <div class="absolute inset-0 bg-gradient-to-br from-blue-600 via-indigo-600 to-purple-700"></div>
      <div class="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjAiIGhlaWdodD0iNjAiIHZpZXdCb3g9IjAgMCA2MCA2MCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48ZyBmaWxsPSJub25lIiBmaWxsLXJ1bGU9ImV2ZW5vZGQiPjxnIGZpbGw9IiNmZmYiIGZpbGwtb3BhY2l0eT0iMC4wNCI+PHBhdGggZD0iTTM2IDM0djZoLTZ2LTZoNnptMC0zMHY2aC02VjRoNnoiLz48L2c+PC9nPjwvc3ZnPg==')] opacity-50"></div>
      <div class="relative px-6 py-5">
        <div class="flex items-center justify-between">
          <div>
            <h1 class="text-xl font-bold text-white flex items-center gap-2">
              <span class="inline-flex items-center justify-center size-8 rounded-lg bg-white/15 backdrop-blur-sm">
                <svg class="size-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" /></svg>
              </span>
              Tools Library
            </h1>
            <p class="text-white/60 text-xs mt-1">{{ externalTools.length }} tools installed</p>
          </div>
          <div class="flex items-center gap-3">
            <div class="relative group">
              <Search class="absolute left-3 top-1/2 -translate-y-1/2 text-white/40 size-4 group-focus-within:text-white/70 transition-colors" />
              <input
                v-model="searchQuery" type="text"
                placeholder="Search tools..."
                class="w-64 bg-white/10 backdrop-blur-sm border border-white/10 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder-white/30 focus:outline-none focus:bg-white/15 focus:border-white/25 focus:ring-1 focus:ring-white/20 transition-all duration-200"
              >
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Tools Grid -->
    <div class="flex-1 overflow-y-auto p-5 bg-[#f8f9fb] dark:bg-[#111]">
      <div v-if="externalTools.length === 0 && !extLoading" class="flex flex-col items-center justify-center h-full text-[var(--text-tertiary)] gap-3">
        <div class="size-16 rounded-2xl bg-gray-100 dark:bg-gray-800 flex items-center justify-center">
          <Box :size="28" class="text-gray-300 dark:text-gray-600" />
        </div>
        <span class="text-sm">{{ t('No external tools installed') }}</span>
        <p class="text-xs opacity-60">Install tools via Skills or the sandbox CLI</p>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4 max-w-[1800px] mx-auto">
        <div v-for="(tool, idx) in filteredExtTools" :key="tool.name"
          class="tool-card group relative rounded-xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-[#1e1e1e] cursor-pointer overflow-hidden"
          :style="{ '--delay': `${Math.min(idx, 20) * 30}ms` }"
          @click="router.push(`/chat/tools/${tool.name}`)">
          <div class="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none">
            <div class="absolute -inset-px rounded-xl bg-gradient-to-r from-emerald-400/20 via-teal-400/20 to-cyan-400/20"></div>
          </div>
          <div class="relative p-4">
            <div class="flex items-start gap-3 mb-2.5">
              <div class="size-10 rounded-xl flex items-center justify-center text-white text-sm font-bold flex-shrink-0 shadow-lg transition-transform duration-300 group-hover:scale-110 group-hover:rotate-3"
                :style="{ background: getToolGradient(tool.name) }">
                {{ tool.name.charAt(0).toUpperCase() }}
              </div>
              <div class="min-w-0 flex-1">
                <h3 class="text-sm font-semibold text-[var(--text-primary)] truncate group-hover:text-transparent group-hover:bg-clip-text group-hover:bg-gradient-to-r group-hover:from-emerald-600 group-hover:to-teal-600 transition-all duration-300">{{ tool.name }}</h3>
                <span class="inline-block mt-0.5 text-[10px] px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-[var(--text-tertiary)] font-mono">{{ tool.file }}</span>
              </div>
              <div class="flex items-center gap-0.5 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                <button @click.stop="handleToggleBlock(tool)" class="p-1.5 rounded-lg transition-colors"
                  :class="tool.blocked ? 'text-amber-500 bg-amber-50 dark:bg-amber-900/20' : 'text-[var(--text-tertiary)] hover:bg-gray-100 dark:hover:bg-gray-800'">
                  <EyeOff v-if="tool.blocked" :size="13" /><Eye v-else :size="13" />
                </button>
                <button @click.stop="confirmDeleteTool(tool)" class="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-900/20 transition-colors">
                  <Trash2 :size="13" />
                </button>
              </div>
            </div>
            <p class="text-xs text-[var(--text-secondary)] leading-relaxed line-clamp-2 min-h-[2.5rem]">{{ tool.description || 'No description' }}</p>
            <div class="mt-3 flex items-center justify-between">
              <span v-if="tool.blocked" class="text-[10px] px-2 py-0.5 rounded-full bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400 font-medium">Blocked</span>
              <span v-else class="text-[10px] text-[var(--text-tertiary)]">Custom tool</span>
              <div class="text-[10px] text-emerald-500 opacity-0 group-hover:opacity-100 transition-all duration-300 translate-x-1 group-hover:translate-x-0 flex items-center gap-0.5">
                Open <svg class="size-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" /></svg>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Delete Dialog -->
    <Teleport to="body">
      <Transition name="modal">
        <div v-if="deleteTarget" class="fixed inset-0 z-[9999] flex items-center justify-center">
          <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="cancelDelete"></div>
          <div class="relative bg-white dark:bg-[#2a2a2a] rounded-2xl shadow-2xl p-6 w-[380px] z-10">
            <div class="flex items-center gap-3 mb-4">
              <div class="size-10 rounded-xl bg-red-50 dark:bg-red-900/20 flex items-center justify-center">
                <Trash2 :size="20" class="text-red-500" />
              </div>
              <div><h3 class="text-sm font-semibold">Delete "{{ deleteTarget.name }}"?</h3><p class="text-xs text-[var(--text-tertiary)]">This action cannot be undone</p></div>
            </div>
            <div class="flex justify-end gap-2">
              <button @click="cancelDelete" class="px-4 py-2 text-sm rounded-lg border border-[var(--border-light)] hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">Cancel</button>
              <button @click="executeDelete" :disabled="deleting" class="px-4 py-2 text-sm rounded-lg bg-red-500 text-white hover:bg-red-600 disabled:opacity-50 transition-all">
                {{ deleting ? 'Deleting...' : 'Delete' }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { Search, Eye, EyeOff, Trash2, Box } from 'lucide-vue-next';
import { getTools, blockTool, deleteTool as apiDeleteTool } from '../api/agent';
import { ExternalToolItem } from '../types/response';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';

const { t } = useI18n();
const router = useRouter();

const searchQuery = ref('');
const externalTools = ref<ExternalToolItem[]>([]);
const extLoading = ref(false);

const gradientPalette = [
  'linear-gradient(135deg, #6366f1, #8b5cf6)',
  'linear-gradient(135deg, #3b82f6, #06b6d4)',
  'linear-gradient(135deg, #ec4899, #8b5cf6)',
  'linear-gradient(135deg, #14b8a6, #22c55e)',
  'linear-gradient(135deg, #f97316, #f59e0b)',
];

const getToolGradient = (name: string) => {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return gradientPalette[Math.abs(hash) % gradientPalette.length];
};

const filteredExtTools = computed(() => {
  if (!searchQuery.value) return externalTools.value;
  return externalTools.value.filter(t => t.name.toLowerCase().includes(searchQuery.value.toLowerCase()));
});

onMounted(async () => {
  extLoading.value = true;
  try {
    externalTools.value = await getTools();
  } catch (e) {
    console.error('Failed to load tools', e);
  } finally {
    extLoading.value = false;
  }
});

const handleToggleBlock = async (tool: ExternalToolItem) => {
  try {
    await blockTool(tool.name, !tool.blocked);
    tool.blocked = !tool.blocked;
  } catch (e) { console.error('Failed to toggle block', e); }
};

const deleteTarget = ref<ExternalToolItem | null>(null);
const deleting = ref(false);
const confirmDeleteTool = (tool: ExternalToolItem) => { deleteTarget.value = tool; };
const cancelDelete = () => { deleteTarget.value = null; };
const executeDelete = async () => {
  if (!deleteTarget.value) return;
  deleting.value = true;
  try {
    await apiDeleteTool(deleteTarget.value.name);
    externalTools.value = externalTools.value.filter(t => t.name !== deleteTarget.value!.name);
    deleteTarget.value = null;
  } catch (e) { console.error('Failed to delete tool', e); }
  finally { deleting.value = false; }
};
</script>

<style scoped>
.tool-card {
  animation: fadeInUp 0.4s ease-out both;
  animation-delay: var(--delay, 0ms);
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}
.modal-enter-active, .modal-leave-active { transition: all 0.2s ease; }
.modal-enter-from, .modal-leave-to { opacity: 0; }
</style>
