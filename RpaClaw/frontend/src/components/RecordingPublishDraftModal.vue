<template>
  <div
    v-if="visible && localDraft"
    class="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/35 p-4 backdrop-blur-sm"
  >
    <div class="flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl dark:bg-gray-950">
      <header class="flex items-center justify-between border-b border-gray-100 px-6 py-4 dark:border-gray-800">
        <div>
          <p class="text-xs font-bold uppercase tracking-[0.22em] text-blue-500">Publish Draft</p>
          <h2 class="mt-1 text-lg font-extrabold text-gray-950 dark:text-gray-50">发布为技能</h2>
        </div>
        <button
          class="rounded-full px-3 py-1 text-sm text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900"
          type="button"
          @click="$emit('close')"
        >
          关闭
        </button>
      </header>

      <main class="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
        <section class="grid gap-4 sm:grid-cols-2">
          <label class="space-y-1">
            <span class="text-xs font-bold text-gray-500">技能名称</span>
            <input
              v-model="localDraft.skill_name"
              data-testid="publish-skill-name"
              class="w-full rounded-2xl border border-gray-200 px-3 py-2 text-sm dark:border-gray-800 dark:bg-gray-900"
            />
          </label>
          <label class="space-y-1">
            <span class="text-xs font-bold text-gray-500">显示标题</span>
            <input
              v-model="localDraft.display_title"
              class="w-full rounded-2xl border border-gray-200 px-3 py-2 text-sm dark:border-gray-800 dark:bg-gray-900"
            />
          </label>
        </section>

        <label class="block space-y-1">
          <span class="text-xs font-bold text-gray-500">技能描述</span>
          <textarea
            v-model="localDraft.description"
            rows="3"
            class="w-full rounded-2xl border border-gray-200 px-3 py-2 text-sm dark:border-gray-800 dark:bg-gray-900"
          />
        </label>

        <section>
          <h3 class="text-sm font-extrabold text-gray-950 dark:text-gray-50">工作流片段</h3>
          <div class="mt-3 space-y-2">
            <div
              v-for="(segment, index) in localDraft.segments"
              :key="segment.id"
              class="rounded-2xl border border-gray-100 bg-gray-50 px-4 py-3 dark:border-gray-800 dark:bg-gray-900"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <div class="text-sm font-bold text-gray-950 dark:text-gray-50">
                    {{ index + 1 }}. {{ segment.title }}
                  </div>
                  <div class="mt-1 text-xs text-gray-500">{{ segment.purpose }}</div>
                </div>
                <span class="rounded-full bg-white px-2 py-1 text-[11px] font-bold uppercase text-blue-600 dark:bg-gray-950">
                  {{ segment.kind }}
                </span>
              </div>
            </div>
          </div>
        </section>

        <section
          v-if="localDraft.warnings.length"
          class="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
        >
          <div class="font-bold">发布前提示</div>
          <ul class="mt-2 list-disc space-y-1 pl-5">
            <li v-for="warning in localDraft.warnings" :key="`${warning.code}-${warning.segment_id || 'run'}`">
              {{ warning.message }}
            </li>
          </ul>
        </section>
      </main>

      <footer class="flex items-center justify-end gap-2 border-t border-gray-100 px-6 py-4 dark:border-gray-800">
        <button
          class="rounded-xl px-4 py-2 text-sm font-bold text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900"
          type="button"
          @click="$emit('close')"
        >
          取消
        </button>
        <button
          data-testid="publish-save"
          class="rounded-xl bg-blue-600 px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
          type="button"
          :disabled="saving || !localDraft.skill_name.trim() || !localDraft.description.trim()"
          @click="$emit('save', localDraft)"
        >
          {{ saving ? '保存中...' : '保存技能' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

import type { SkillPublishDraft } from '@/types/recording'

const props = defineProps<{
  visible: boolean
  draft: SkillPublishDraft | null
  saving: boolean
}>()

defineEmits<{
  close: []
  save: [draft: SkillPublishDraft]
}>()

const localDraft = ref<SkillPublishDraft | null>(null)

watch(
  () => props.draft,
  (draft) => {
    localDraft.value = draft ? JSON.parse(JSON.stringify(draft)) as SkillPublishDraft : null
  },
  { immediate: true },
)
</script>
