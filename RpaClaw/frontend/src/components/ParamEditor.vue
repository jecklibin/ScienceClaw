<template>
  <div class="flex h-full flex-col overflow-hidden bg-white text-slate-900 dark:bg-[#15171d] dark:text-slate-100">
    <!-- Header Bar -->
    <div class="flex-shrink-0 flex items-center justify-between bg-[#f3f4f5] px-4 py-3 dark:bg-[#1f2229]">
      <div class="flex items-center gap-2">
        <SlidersHorizontal class="size-4 text-violet-600 dark:text-violet-300" />
        <span class="text-sm font-bold text-gray-900 dark:text-slate-100">{{ t('Parameter Editor') }}</span>
      </div>
      <div class="flex items-center gap-2">
        <button
          v-if="mode === 'form' && !isReadOnly"
          type="button"
          @click="addParameter"
          class="flex items-center gap-1.5 rounded-md bg-[#f0dbff] px-3 py-1.5 text-xs font-semibold text-[#6500ac] transition-colors hover:bg-[#ddb7ff] dark:bg-[#831BD7] dark:text-white dark:shadow-[0_0_0_1px_rgba(255,255,255,0.10)] dark:hover:bg-[#6f16b8]"
        >
          <Plus class="size-3.5" />
          {{ t('Add Parameter') }}
        </button>
        <button
          type="button"
          @click="toggleMode"
          class="flex items-center gap-1.5 rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 transition-colors hover:bg-[#e7e8e9] dark:bg-[#2c3038] dark:text-slate-100 dark:shadow-[0_0_0_1px_rgba(255,255,255,0.08)] dark:hover:bg-[#363b45]"
        >
          <Code2 v-if="mode === 'form'" class="size-3.5" />
          <LayoutList v-else class="size-3.5" />
          {{ mode === 'form' ? t('Text Mode') : t('Form Mode') }}
        </button>
      </div>
    </div>

    <!-- Form Mode -->
    <div v-if="mode === 'form'" class="flex-1 space-y-3 overflow-y-auto bg-[#f3f4f5] p-4 dark:bg-[#15171d]">
        <div v-if="paramList.length === 0" class="flex flex-col items-center justify-center py-16 text-gray-400 dark:text-slate-500">
          <SlidersHorizontal class="size-10 opacity-30 mb-3" />
          <p class="text-sm">{{ t('No parameters configured') }}</p>
          <button
            v-if="!isReadOnly"
            type="button"
            @click="addParameter"
            class="mt-3 flex items-center gap-1.5 rounded-md bg-[#831BD7] px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-[#6500ac] dark:bg-[#831BD7] dark:shadow-[0_0_0_1px_rgba(255,255,255,0.10)] dark:hover:bg-[#6f16b8]"
          >
          <Plus class="size-3.5" />
          {{ t('Add Parameter') }}
        </button>
      </div>

      <!-- Parameter Cards -->
      <div
        v-for="(param, index) in paramList"
        :key="param.key"
        class="rounded-lg border border-transparent bg-white p-4 transition-all hover:bg-white/95 hover:shadow-[0_10px_30px_rgba(25,28,30,0.05)] dark:border-white/[0.06] dark:bg-[#24262c] dark:hover:bg-[#282b32]"
        :class="param.sensitive ? 'border-l-4 border-pink-400' : ''"
      >
        <div class="flex justify-between items-start mb-3">
          <div class="flex items-center gap-3">
            <div class="rounded-md p-2" :class="param.sensitive ? 'bg-pink-50 dark:bg-pink-500/12' : 'bg-[#f3f4f5] dark:bg-white/[0.07]'">
              <Lock v-if="param.sensitive" class="size-4 text-pink-500 dark:text-pink-300" />
              <User v-else-if="param.type === 'string'" class="size-4 text-gray-500 dark:text-slate-300" />
              <Clock v-else-if="param.type === 'integer' || param.type === 'number'" class="size-4 text-gray-500 dark:text-slate-300" />
              <Settings2 v-else class="size-4 text-gray-500 dark:text-slate-300" />
            </div>
            <div>
              <input
                v-if="!isReadOnly"
                v-model="param.name"
                @input="emitChange"
                class="w-40 border-none bg-transparent p-0 text-sm font-bold text-gray-900 focus:outline-none focus:ring-0 dark:text-slate-100 dark:placeholder:text-slate-500"
                :placeholder="t('param_name')"
              />
              <p v-else class="text-sm font-bold text-gray-900 dark:text-slate-100">{{ param.name }}</p>
              <div class="flex items-center gap-2 mt-0.5">
                <select
                  v-if="!isReadOnly"
                  v-model="param.type"
                  @change="emitChange"
                  class="cursor-pointer border-none bg-transparent p-0 text-[10px] font-semibold uppercase tracking-widest text-gray-400 focus:ring-0 dark:text-slate-400"
                >
                  <option value="string">String</option>
                  <option value="integer">Integer</option>
                  <option value="number">Number</option>
                  <option value="boolean">Boolean</option>
                </select>
                <span v-else class="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-slate-400">{{ param.type }}</span>
              </div>
            </div>
          </div>
          <div v-if="!isReadOnly" class="flex items-center gap-2">
            <button
              type="button"
              @click="param.sensitive = !param.sensitive; emitChange()"
              class="rounded-md px-2 py-0.5 text-[10px] font-bold uppercase transition-colors"
              :class="param.sensitive
                ? 'bg-pink-100 text-pink-600 hover:bg-pink-200 dark:bg-pink-500/16 dark:text-pink-200 dark:hover:bg-pink-500/24'
                : 'bg-green-100 text-green-600 hover:bg-green-200 dark:bg-emerald-500/16 dark:text-emerald-200 dark:hover:bg-emerald-500/24'"
            >
              {{ param.sensitive ? t('Sensitive') : t('Public') }}
            </button>
            <button
              type="button"
              @click="removeParameter(index)"
              class="p-1 text-gray-400 hover:text-red-500 transition-colors"
            >
              <Trash2 class="size-4" />
            </button>
          </div>
        </div>

        <!-- Value Input -->
        <div class="flex gap-2">
          <div class="relative flex-1">
            <input
              v-if="!isReadOnly"
              v-model="param.original_value"
              @input="emitChange"
              class="w-full rounded-md border-none bg-[#f3f4f5] px-3 py-2 text-sm font-medium text-slate-900 transition-all placeholder:text-slate-400 focus:bg-white focus:ring-2 focus:ring-violet-200 dark:bg-[#181a20] dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:bg-[#1f2229] dark:focus:ring-violet-400/35"
              :type="param.sensitive && !param.showPassword ? 'password' : 'text'"
              :placeholder="param.sensitive ? '********' : t('Enter value...')"
            />
            <div v-else class="w-full rounded-md bg-[#f3f4f5] px-3 py-2 text-sm font-medium text-gray-700 dark:bg-[#181a20] dark:text-slate-200">
              {{ param.sensitive ? '********' : (param.original_value || '-') }}
            </div>
            <button
              v-if="param.sensitive && !isReadOnly"
              type="button"
              @click="param.showPassword = !param.showPassword"
              class="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 transition-colors hover:text-violet-600 dark:text-slate-500 dark:hover:text-violet-300"
            >
              <EyeOff v-if="param.showPassword" class="size-4" />
              <Eye v-else class="size-4" />
            </button>
          </div>
          <button
            v-if="param.sensitive && !isReadOnly"
            type="button"
            @click="openVaultPicker(index)"
            class="flex items-center gap-1.5 whitespace-nowrap rounded-md bg-[#f0dbff] px-3 text-xs font-semibold text-[#6500ac] transition-all hover:bg-[#ddb7ff] dark:bg-[#831BD7] dark:text-white dark:shadow-[0_0_0_1px_rgba(255,255,255,0.10)] dark:hover:bg-[#6f16b8]"
          >
            <KeyRound class="size-3.5" />
            {{ t('Link Vault') }}
          </button>
        </div>

        <!-- Credential Link Display -->
        <div v-if="param.credential_id" class="mt-2 flex items-center gap-1.5">
          <Link2 class="size-3 text-gray-400 dark:text-slate-500" />
          <span class="text-[11px] font-semibold text-gray-500 dark:text-slate-400">
            {{ t('Linked to') }}
            <span class="text-violet-600 dark:text-violet-300">{{ param.credential_id }}</span>
          </span>
          <button v-if="!isReadOnly" type="button" @click="param.credential_id = ''; emitChange()" class="ml-1 text-gray-400 hover:text-red-500">
            <X class="size-3" />
          </button>
        </div>

        <!-- Required Checkbox -->
        <div class="mt-2 flex items-center gap-2">
          <input
            v-if="!isReadOnly"
            type="checkbox"
            :id="`req-${index}`"
            v-model="param.required"
            @change="emitChange"
            class="size-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-200 dark:border-slate-600 dark:bg-[#181a20] dark:focus:ring-violet-400/35"
          />
          <span
            v-else
            class="inline-flex size-3.5 items-center justify-center rounded border text-[9px]"
            :class="param.required ? 'border-violet-300 bg-violet-50 text-violet-600 dark:border-violet-400/40 dark:bg-violet-500/12 dark:text-violet-200' : 'border-gray-200 bg-gray-50 text-gray-300 dark:border-slate-700 dark:bg-[#181a20] dark:text-slate-600'"
          >
            {{ param.required ? 'Y' : '' }}
          </span>
          <label :for="`req-${index}`" class="text-xs text-gray-500 dark:text-slate-400">{{ t('Required') }}</label>
        </div>
      </div>
    </div>

    <!-- Text Mode (Monaco Editor) -->
    <div v-else class="flex-1 h-0 overflow-hidden">
      <MonacoEditor
        :value="textContent"
        language="json"
        :read-only="isReadOnly"
        theme="vs"
        :minimap="false"
        :word-wrap="'on'"
        @change="onTextChange"
      />
    </div>

    <!-- Vault Picker Modal -->
    <Teleport to="body">
      <div v-if="showVaultPicker" class="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" @click.self="showVaultPicker = false">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
          <div class="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
            <h3 class="text-sm font-bold text-gray-900">{{ t('Select Credential') }}</h3>
            <button @click="showVaultPicker = false" class="p-1 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-50">
              <X class="size-4" />
            </button>
          </div>
          <div class="p-4 max-h-80 overflow-y-auto">
            <div v-if="credentials.length === 0" class="text-center py-8 text-sm text-gray-400">
              {{ t('No credentials available') }}
            </div>
            <div
              v-for="cred in credentials"
              :key="cred.id"
              class="flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer hover:bg-violet-50 transition-colors"
              @click="linkCredential(cred)"
            >
              <div class="p-1.5 bg-violet-100 rounded-lg">
                <KeyRound class="size-3.5 text-violet-600" />
              </div>
              <div class="min-w-0 flex-1">
                <p class="text-sm font-semibold text-gray-900 truncate">{{ cred.name }}</p>
                <p class="text-xs text-gray-400 truncate">{{ cred.username }} {{ cred.domain ? `@ ${cred.domain}` : '' }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  SlidersHorizontal, Plus, Code2, LayoutList, Lock, User, Clock, Settings2,
  Trash2, Eye, EyeOff, KeyRound, Link2, X
} from 'lucide-vue-next';
import MonacoEditor from './ui/MonacoEditor.vue';
import { listCredentials, type Credential } from '../api/credential';

const { t } = useI18n();

interface ParamItem {
  key: number;
  name: string;
  type: string;
  sensitive: boolean;
  credential_id: string;
  original_value: string;
  required: boolean;
  description: string;
  showPassword: boolean;
}

const props = defineProps<{
  content: string;
  readonly?: boolean;
}>();

const emit = defineEmits<{
  change: [value: string];
}>();

const mode = ref<'form' | 'text'>('form');
const isReadOnly = computed(() => props.readonly === true);
const paramList = ref<ParamItem[]>([]);
const textContent = ref('');
let nextKey = 0;

const credentials = ref<Credential[]>([]);
const showVaultPicker = ref(false);
const vaultPickerIndex = ref(-1);

const parseParams = (json: string) => {
  try {
    const obj = JSON.parse(json);
    const list: ParamItem[] = [];
    for (const [name, info] of Object.entries(obj)) {
      const p = info as any;
      list.push({
        key: nextKey++,
        name,
        type: p.type || 'string',
        sensitive: !!p.sensitive,
        credential_id: p.credential_id || '',
        original_value: p.original_value || '',
        required: !!p.required,
        description: p.description || '',
        showPassword: false,
      });
    }
    return list;
  } catch {
    return [];
  }
};

const serializeParams = (): string => {
  const obj: Record<string, any> = {};
  for (const p of paramList.value) {
    if (!p.name) continue;
    const entry: any = {
      type: p.type,
      description: p.description,
      sensitive: p.sensitive,
      required: p.required,
      original_value: p.original_value,
    };
    if (p.credential_id) {
      entry.credential_id = p.credential_id;
    }
    obj[p.name] = entry;
  }
  return JSON.stringify(obj, null, 2);
};

const emitChange = () => {
  const json = serializeParams();
  textContent.value = json;
  emit('change', json);
};

const onTextChange = (value: string) => {
  textContent.value = value;
  // Try to sync form
  const parsed = parseParams(value);
  if (parsed.length > 0 || value.trim() === '{}') {
    paramList.value = parsed;
  }
  emit('change', value);
};

const toggleMode = () => {
  if (mode.value === 'form') {
    textContent.value = serializeParams();
    mode.value = 'text';
  } else {
    paramList.value = parseParams(textContent.value);
    mode.value = 'form';
  }
};

const addParameter = () => {
  paramList.value.push({
    key: nextKey++,
    name: '',
    type: 'string',
    sensitive: false,
    credential_id: '',
    original_value: '',
    required: false,
    description: '',
    showPassword: false,
  });
};

const removeParameter = (index: number) => {
  paramList.value.splice(index, 1);
  emitChange();
};

const openVaultPicker = async (index: number) => {
  vaultPickerIndex.value = index;
  try {
    credentials.value = await listCredentials();
  } catch (e) {
    console.error('Failed to load credentials', e);
  }
  showVaultPicker.value = true;
};

const linkCredential = (cred: Credential) => {
  const idx = vaultPickerIndex.value;
  if (idx >= 0 && idx < paramList.value.length) {
    paramList.value[idx].credential_id = cred.id;
  }
  showVaultPicker.value = false;
  emitChange();
};

// Initialize from props
watch(() => props.content, (val) => {
  paramList.value = parseParams(val);
  textContent.value = val;
}, { immediate: true });
</script>
