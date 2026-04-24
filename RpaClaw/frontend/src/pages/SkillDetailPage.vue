<template>
  <div class="flex h-full w-full flex-col overflow-hidden">
    <div class="relative flex-shrink-0 overflow-hidden">
      <div class="absolute inset-0" :style="{ background: heroGradient }"></div>
      <div class="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjAiIGhlaWdodD0iNjAiIHZpZXdCb3g9IjAgMCA2MCA2MCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48ZyBmaWxsPSJub25lIiBmaWxsLXJ1bGU9ImV2ZW5vZGQiPjxnIGZpbGw9IiNmZmYiIGZpbGwtb3BhY2l0eT0iMC4wNCI+PHBhdGggZD0iTTM2IDM0djZoLTZ2LTZoNnptMC0zMHY2aC02VjRoNnoiLz48L2c+PC9nPjwvc3ZnPg==')] opacity-50"></div>
      <div class="relative px-6 py-4">
        <div class="flex items-center gap-4">
          <button @click="goBack" class="rounded-lg p-2 -ml-2 text-white/70 transition-colors hover:bg-white/10 hover:text-white">
            <ArrowLeft class="size-5" />
          </button>
          <div class="flex size-11 items-center justify-center rounded-xl bg-white/15 text-lg font-bold text-white shadow-lg backdrop-blur-sm">
            {{ skillName.charAt(0).toUpperCase() }}
          </div>
          <div class="min-w-0 flex-1">
            <h1 class="truncate text-base font-bold text-white">{{ currentTitle }}</h1>
            <div v-if="showRecordedOverview && activeTab === 'overview'" class="mt-1 text-xs text-white/70">
              {{ t('Recorded skill files and script entry remain available in Files.') }}
            </div>
            <div v-else class="mt-1 flex items-center gap-1 text-xs text-white/50">
              <span class="cursor-pointer transition-colors hover:text-white/80" @click="navigateToRoot">root</span>
              <template v-for="(part, index) in pathParts" :key="index">
                <span>/</span>
                <span class="cursor-pointer transition-colors hover:text-white/80" @click="navigateToPart(index)">{{ part }}</span>
              </template>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <template v-if="showFileActions && editMode">
              <button
                @click="cancelEdit"
                class="rounded-lg px-3 py-1.5 text-sm font-medium text-white/80 transition-colors hover:bg-white/10 hover:text-white"
              >
                {{ t('Cancel') }}
              </button>
              <button
                @click="saveFile"
                :disabled="!dirty || saving"
                class="flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-sm font-semibold transition-all"
                :class="dirty ? 'bg-white text-violet-700 shadow-lg hover:bg-white/90' : 'cursor-not-allowed bg-white/20 text-white/40'"
              >
                <Save class="size-3.5" />
                {{ saving ? t('Saving...') : t('Save') }}
              </button>
            </template>
            <button
              v-else-if="showFileActions && !isBuiltin"
              @click="enterEdit"
              class="flex items-center gap-1.5 rounded-lg bg-white/15 px-4 py-1.5 text-sm font-semibold text-white transition-colors backdrop-blur-sm hover:bg-white/25"
            >
              <Pencil class="size-3.5" />
              {{ t('Edit') }}
            </button>
            <span
              v-else-if="isBuiltin"
              class="rounded-full bg-white/10 px-3 py-1 text-xs font-medium text-white/60"
            >
              {{ t('Built-in') }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-hidden">
      <ResourceDetailShell v-if="showRecordedOverview" v-model:tab="activeTab">
        <div v-if="activeTab === 'overview'" class="h-full overflow-y-auto bg-[#f5f7fb] px-6 py-6 dark:bg-[#101115]">
          <div class="mx-auto max-w-6xl space-y-6">
            <ResourceOverviewHeader
              :title="recordedDetail?.name || skillName"
              :description="recordedDetail?.description || ''"
              :kind-label="t('Recorded Skill')"
              :badges="recordedSkillBadges"
              :counts="recordedSummaryCards"
            />

            <section class="rounded-3xl border border-slate-200 bg-white/90 p-6 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
              <h2 class="text-lg font-black text-slate-900 dark:text-slate-100">{{ t('Basic Info') }}</h2>
              <div class="mt-4 grid gap-4 sm:grid-cols-2">
                <div class="rounded-2xl bg-slate-50 px-4 py-4 dark:bg-white/[0.03]">
                  <div class="text-[11px] font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('Entry Script') }}</div>
                  <div class="mt-2 text-sm font-semibold text-slate-900 dark:text-slate-100">{{ recordedDetail?.entry_script || 'skill.py' }}</div>
                </div>
                <div class="rounded-2xl bg-slate-50 px-4 py-4 dark:bg-white/[0.03]">
                  <div class="text-[11px] font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('Generated at') }}</div>
                  <div class="mt-2 text-sm font-semibold text-slate-900 dark:text-slate-100">{{ recordedDetail?.generated_at || '—' }}</div>
                </div>
              </div>
            </section>

            <ResourceParamList
              :params="recordedDetail?.params || {}"
              :title="t('Parameters')"
            />

            <ResourceStepList
              :steps="recordedDetail?.steps || []"
              :title="t('Steps')"
            />

            <section class="rounded-3xl border border-slate-200 bg-white/90 p-6 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
              <h2 class="text-lg font-black text-slate-900 dark:text-slate-100">{{ t('Execution Info') }}</h2>
              <div class="mt-4 grid gap-4 lg:grid-cols-[minmax(0,240px)_1fr]">
                <div class="rounded-2xl bg-slate-50 px-4 py-4 dark:bg-white/[0.03]">
                  <div class="text-[11px] font-bold uppercase tracking-wide text-slate-400 dark:text-slate-500">{{ t('Artifacts') }}</div>
                  <ul class="mt-2 space-y-2 text-sm text-slate-700 dark:text-slate-300">
                    <li v-for="artifact in recordedDetail?.artifacts || []" :key="artifact">{{ artifact }}</li>
                  </ul>
                </div>
                <div class="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-4 text-sm text-slate-500 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-400">
                  {{ t('Recorded skill files and script entry remain available in Files.') }}
                </div>
              </div>
            </section>
          </div>
        </div>

        <div v-else class="flex h-full overflow-hidden bg-[#f8f9fb] dark:bg-[#111]">
          <div class="flex w-64 flex-col overflow-hidden border-r border-gray-100 bg-white/80 backdrop-blur-sm dark:border-gray-800 dark:bg-[#1a1a1a]/80">
            <div class="flex items-center justify-between border-b border-gray-100 px-3 py-2.5 dark:border-gray-800">
              <span class="truncate text-[10px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">
                {{ currentPath || 'Root' }}
              </span>
              <button
                v-if="currentPath"
                @click="navigateUp"
                class="flex items-center gap-0.5 text-[10px] text-violet-600 transition-colors hover:text-violet-700 dark:text-violet-400"
              >
                <ArrowLeft class="size-3" /> Up
              </button>
            </div>
            <div class="flex-1 overflow-y-auto p-2">
              <div v-if="loading" class="flex flex-col items-center justify-center gap-2 py-8">
                <div class="relative size-8">
                  <div class="absolute inset-0 rounded-full border-2 border-violet-100 dark:border-violet-900"></div>
                  <div class="absolute inset-0 animate-spin rounded-full border-2 border-violet-500 border-t-transparent"></div>
                </div>
              </div>
              <div v-else-if="fileTree.length === 0" class="py-8 text-center text-xs text-[var(--text-tertiary)]">Empty directory</div>
              <template v-else>
                <div
                  v-if="currentPath"
                  class="mb-1 flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-xs text-[var(--text-tertiary)] transition-all duration-200 hover:bg-gray-50 dark:hover:bg-white/5"
                  @click="navigateUp"
                >
                  <ArrowLeft class="size-3 opacity-50" />
                  <span>..</span>
                </div>
                <div
                  v-for="(item, idx) in fileTree"
                  :key="item.path"
                  class="file-item flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-xs transition-all duration-200"
                  :class="selectedFile === item.path ? 'bg-violet-50 font-medium text-violet-700 dark:bg-violet-900/20 dark:text-violet-300' : 'text-[var(--text-secondary)] hover:bg-gray-50 dark:hover:bg-white/5'"
                  :style="{ '--delay': `${idx * 20}ms` }"
                  @click="handleItemClick(item)"
                >
                  <Folder v-if="item.type === 'directory'" class="size-4 flex-shrink-0 text-amber-400" />
                  <FileText v-else class="size-4 flex-shrink-0 text-gray-400" />
                  <span class="truncate">{{ item.name }}</span>
                  <span v-if="editMode && dirtyFiles.has(item.path)" class="ml-auto size-2 flex-shrink-0 rounded-full bg-amber-400"></span>
                </div>
              </template>
            </div>
          </div>

          <div class="flex flex-1 flex-col overflow-hidden">
            <div v-if="selectedFile" class="flex flex-1 flex-col overflow-hidden">
              <div v-if="error" class="flex flex-1 items-center justify-center p-6">
                <div class="w-full max-w-lg rounded-xl border border-red-200 bg-red-50 p-5 text-center dark:border-red-800/50 dark:bg-red-900/10">
                  <p class="text-sm font-medium text-red-600 dark:text-red-400">Error loading file</p>
                  <p class="mt-1 text-xs text-red-500/70">{{ error }}</p>
                </div>
              </div>

              <ParamEditor
                v-else-if="isParamsJson"
                :content="fileContent || '{}'"
                :readonly="!editMode"
                class="flex-1"
                @change="onContentChange"
              />

              <FileViewer
                v-else
                :file-name="selectedFileName"
                :skill-name="skillName"
                :path="selectedFile"
                :content="fileContent"
                :editable="editMode"
                class="flex-1"
                @change="onContentChange"
              />
            </div>
            <div v-else class="flex flex-1 items-center justify-center">
              <div class="text-center">
                <div class="mx-auto mb-3 flex size-20 items-center justify-center rounded-2xl bg-gray-50 dark:bg-gray-900">
                  <FileSearch class="size-8 opacity-20" />
                </div>
                <p class="text-sm text-[var(--text-tertiary)]">{{ t('Select a file to view content') }}</p>
                <p class="mt-1 text-xs text-[var(--text-tertiary)] opacity-50">Browse the file tree on the left</p>
              </div>
            </div>
          </div>
        </div>
      </ResourceDetailShell>

      <div v-else class="flex h-full overflow-hidden bg-[#f8f9fb] dark:bg-[#111]">
        <div class="flex w-64 flex-col overflow-hidden border-r border-gray-100 bg-white/80 backdrop-blur-sm dark:border-gray-800 dark:bg-[#1a1a1a]/80">
          <div class="flex items-center justify-between border-b border-gray-100 px-3 py-2.5 dark:border-gray-800">
            <span class="truncate text-[10px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">
              {{ currentPath || 'Root' }}
            </span>
            <button
              v-if="currentPath"
              @click="navigateUp"
              class="flex items-center gap-0.5 text-[10px] text-violet-600 transition-colors hover:text-violet-700 dark:text-violet-400"
            >
              <ArrowLeft class="size-3" /> Up
            </button>
          </div>
          <div class="flex-1 overflow-y-auto p-2">
            <div v-if="loading" class="flex flex-col items-center justify-center gap-2 py-8">
              <div class="relative size-8">
                <div class="absolute inset-0 rounded-full border-2 border-violet-100 dark:border-violet-900"></div>
                <div class="absolute inset-0 animate-spin rounded-full border-2 border-violet-500 border-t-transparent"></div>
              </div>
            </div>
            <div v-else-if="fileTree.length === 0" class="py-8 text-center text-xs text-[var(--text-tertiary)]">Empty directory</div>
            <template v-else>
              <div
                v-if="currentPath"
                class="mb-1 flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-xs text-[var(--text-tertiary)] transition-all duration-200 hover:bg-gray-50 dark:hover:bg-white/5"
                @click="navigateUp"
              >
                <ArrowLeft class="size-3 opacity-50" />
                <span>..</span>
              </div>
              <div
                v-for="(item, idx) in fileTree"
                :key="item.path"
                class="file-item flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-xs transition-all duration-200"
                :class="selectedFile === item.path ? 'bg-violet-50 font-medium text-violet-700 dark:bg-violet-900/20 dark:text-violet-300' : 'text-[var(--text-secondary)] hover:bg-gray-50 dark:hover:bg-white/5'"
                :style="{ '--delay': `${idx * 20}ms` }"
                @click="handleItemClick(item)"
              >
                <Folder v-if="item.type === 'directory'" class="size-4 flex-shrink-0 text-amber-400" />
                <FileText v-else class="size-4 flex-shrink-0 text-gray-400" />
                <span class="truncate">{{ item.name }}</span>
                <span v-if="editMode && dirtyFiles.has(item.path)" class="ml-auto size-2 flex-shrink-0 rounded-full bg-amber-400"></span>
              </div>
            </template>
          </div>
        </div>

        <div class="flex flex-1 flex-col overflow-hidden">
          <div v-if="selectedFile" class="flex flex-1 flex-col overflow-hidden">
            <div v-if="error" class="flex flex-1 items-center justify-center p-6">
              <div class="w-full max-w-lg rounded-xl border border-red-200 bg-red-50 p-5 text-center dark:border-red-800/50 dark:bg-red-900/10">
                <p class="text-sm font-medium text-red-600 dark:text-red-400">Error loading file</p>
                <p class="mt-1 text-xs text-red-500/70">{{ error }}</p>
              </div>
            </div>

            <ParamEditor
              v-else-if="isParamsJson"
              :content="fileContent || '{}'"
              :readonly="!editMode"
              class="flex-1"
              @change="onContentChange"
            />

            <FileViewer
              v-else
              :file-name="selectedFileName"
              :skill-name="skillName"
              :path="selectedFile"
              :content="fileContent"
              :editable="editMode"
              class="flex-1"
              @change="onContentChange"
            />
          </div>
          <div v-else class="flex flex-1 items-center justify-center">
            <div class="text-center">
              <div class="mx-auto mb-3 flex size-20 items-center justify-center rounded-2xl bg-gray-50 dark:bg-gray-900">
                <FileSearch class="size-8 opacity-20" />
              </div>
              <p class="text-sm text-[var(--text-tertiary)]">{{ t('Select a file to view content') }}</p>
              <p class="mt-1 text-xs text-[var(--text-tertiary)] opacity-50">Browse the file tree on the left</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { ArrowLeft, Folder, FileText, FileSearch, Pencil, Save } from 'lucide-vue-next';
import { getSkillDetail, getSkillFiles, getSkills, readSkillFile, type RecordedSkillDetail, writeSkillFile } from '../api/agent';
import FileViewer from '../components/FileViewer.vue';
import ParamEditor from '../components/ParamEditor.vue';
import ResourceDetailShell from '../components/resourceDetail/ResourceDetailShell.vue';
import ResourceOverviewHeader from '../components/resourceDetail/ResourceOverviewHeader.vue';
import ResourceParamList from '../components/resourceDetail/ResourceParamList.vue';
import ResourceStepList from '../components/resourceDetail/ResourceStepList.vue';
import { buildResourceSummaryCounts } from '../utils/resourceDetailView';
import { canUseRecordedSkillOverview } from '../utils/skillDetailMode';
import { pickDefaultSkillFile } from '../utils/skillDetailView';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const skillName = route.params.skillName as string;

const gradients = [
  'linear-gradient(135deg, #8b5cf6 0%, #a855f7 50%, #d946ef 100%)',
  'linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #a855f7 100%)',
  'linear-gradient(135deg, #ec4899 0%, #f43f5e 50%, #f97316 100%)',
  'linear-gradient(135deg, #d946ef 0%, #ec4899 50%, #f43f5e 100%)',
  'linear-gradient(135deg, #7c3aed 0%, #6366f1 50%, #3b82f6 100%)',
];

const heroGradient = computed(() => {
  let hash = 0;
  for (let index = 0; index < skillName.length; index += 1) {
    hash = skillName.charCodeAt(index) + ((hash << 5) - hash);
  }
  return gradients[Math.abs(hash) % gradients.length];
});

const loading = ref(true);
const error = ref<string | null>(null);
const fileTree = ref<any[]>([]);
const currentPath = ref('');
const selectedFile = ref<string | null>(null);
const fileContent = ref<string | null>(null);
const isBuiltin = ref(false);
const editMode = ref(false);
const dirty = ref(false);
const saving = ref(false);
const editedContent = ref<string | null>(null);
const dirtyFiles = ref(new Set<string>());
const detailMode = ref<'legacy-files' | 'recorded-overview'>('legacy-files');
const activeTab = ref<'overview' | 'files'>('overview');
const recordedDetail = ref<RecordedSkillDetail | null>(null);

const selectedFileName = computed(() => selectedFile.value?.split('/').pop() || '');
const pathParts = computed(() => currentPath.value ? currentPath.value.split('/') : []);
const isParamsJson = computed(() => selectedFileName.value === 'params.json');
const showRecordedOverview = computed(() => detailMode.value === 'recorded-overview' && Boolean(recordedDetail.value));
const showFileActions = computed(() => !showRecordedOverview.value || activeTab.value === 'files');
const currentTitle = computed(() => showRecordedOverview.value && activeTab.value === 'overview'
  ? (recordedDetail.value?.name || skillName)
  : skillName);
const recordedSummaryCounts = computed(() => buildResourceSummaryCounts({
  params: recordedDetail.value?.params || {},
  steps: recordedDetail.value?.steps || [],
  files: recordedDetail.value?.files || [],
}));
const recordedSummaryCards = computed(() => ([
  { label: t('Parameters'), value: recordedSummaryCounts.value.params },
  { label: t('Steps'), value: recordedSummaryCounts.value.steps },
  { label: t('Files'), value: recordedSummaryCounts.value.files },
]));
const recordedSkillBadges = computed(() => {
  const badges = [recordedDetail.value?.entry_script || 'skill.py'];
  if (recordedDetail.value?.generated_at) {
    badges.push(recordedDetail.value.generated_at);
  }
  return badges;
});

const isTextFile = (filename: string) => {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  return ['md', 'markdown', 'txt', 'log', 'csv', 'ini', 'conf', 'cfg', 'env', 'js', 'ts', 'jsx', 'tsx', 'py', 'java', 'c', 'cpp', 'h', 'hpp', 'go', 'rs', 'php', 'rb', 'swift', 'kt', 'scala', 'cs', 'html', 'css', 'scss', 'less', 'json', 'xml', 'yaml', 'yml', 'sh', 'bash', 'zsh', 'bat', 'ps1', 'dockerfile', 'makefile', 'sql'].includes(ext);
};

const loadFiles = async () => {
  loading.value = true;
  try {
    fileTree.value = await getSkillFiles(skillName, currentPath.value);
  } catch (loadError) {
    console.error(loadError);
  } finally {
    loading.value = false;
  }
};

const selectFile = async (item: any) => {
  error.value = null;
  selectedFile.value = item.path;
  fileContent.value = null;
  editedContent.value = null;
  dirty.value = false;
  if (!isTextFile(item.name)) return;

  try {
    const response = await readSkillFile(skillName, item.path);
    fileContent.value = response.content;
  } catch (readError: any) {
    error.value = readError.message || 'Failed to load content';
  }
};

const ensureDefaultFileSelected = async () => {
  if (selectedFile.value) return;
  const defaultFile = pickDefaultSkillFile(fileTree.value) || fileTree.value.find((item) => item.type === 'file');
  if (defaultFile) {
    await selectFile(defaultFile);
  }
};

const loadDetailMode = async () => {
  let detail: RecordedSkillDetail | null = null;
  try {
    detail = await getSkillDetail(skillName);
  } catch {
    detail = null;
  }

  if (canUseRecordedSkillOverview({ files: fileTree.value, detail })) {
    recordedDetail.value = detail;
    detailMode.value = 'recorded-overview';
    activeTab.value = 'overview';
    return;
  }

  detailMode.value = 'legacy-files';
  recordedDetail.value = null;
  await ensureDefaultFileSelected();
};

const handleItemClick = async (item: any) => {
  if (item.type === 'directory') {
    currentPath.value = item.path;
    selectedFile.value = null;
    fileContent.value = null;
    editedContent.value = null;
    await loadFiles();
    return;
  }
  await selectFile(item);
};

const onContentChange = (value: string) => {
  editedContent.value = value;
  dirty.value = true;
  if (selectedFile.value) {
    dirtyFiles.value.add(selectedFile.value);
  }
};

const enterEdit = () => {
  editMode.value = true;
};

const cancelEdit = () => {
  editMode.value = false;
  dirty.value = false;
  editedContent.value = null;
  dirtyFiles.value.clear();
};

const saveFile = async () => {
  if (!selectedFile.value || editedContent.value === null) return;
  saving.value = true;
  try {
    await writeSkillFile(skillName, selectedFile.value, editedContent.value);
    fileContent.value = editedContent.value;
    dirty.value = false;
    dirtyFiles.value.delete(selectedFile.value);
    editedContent.value = null;
  } catch (saveError: any) {
    error.value = saveError.message || 'Failed to save file';
  } finally {
    saving.value = false;
  }
};

const navigateUp = async () => {
  if (!currentPath.value) return;
  const parts = currentPath.value.split('/');
  parts.pop();
  currentPath.value = parts.join('/');
  selectedFile.value = null;
  fileContent.value = null;
  await loadFiles();
};

const navigateToPart = async (index: number) => {
  currentPath.value = pathParts.value.slice(0, index + 1).join('/');
  selectedFile.value = null;
  fileContent.value = null;
  await loadFiles();
};

const navigateToRoot = async () => {
  currentPath.value = '';
  selectedFile.value = null;
  fileContent.value = null;
  await loadFiles();
};

const goBack = () => router.back();

watch(activeTab, async (nextTab) => {
  if (showRecordedOverview.value && nextTab === 'files') {
    await ensureDefaultFileSelected();
  }
});

onMounted(async () => {
  await loadFiles();
  await loadDetailMode();

  try {
    const skills = await getSkills();
    const skill = skills.find((item) => item.name === skillName);
    if (skill) {
      isBuiltin.value = Boolean(skill.builtin);
    }
  } catch {
    // ignore built-in detection failures
  }
});
</script>

<style scoped>
.file-item {
  animation: fileFadeIn 0.3s ease-out both;
  animation-delay: var(--delay, 0ms);
}

@keyframes fileFadeIn {
  from { opacity: 0; transform: translateX(-8px); }
  to { opacity: 1; transform: translateX(0); }
}

::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #e0e0e0; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #ccc; }
</style>
