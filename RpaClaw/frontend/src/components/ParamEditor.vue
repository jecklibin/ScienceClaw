<template>
  <div class="h-full flex flex-col overflow-hidden">
    <!-- Header Bar -->
    <div class="flex-shrink-0 flex items-center justify-between px-5 py-3 border-b border-gray-100 bg-white">
      <div class="flex items-center gap-2">
        <SlidersHorizontal class="size-4 text-violet-600" />
        <span class="text-sm font-bold text-gray-900">{{ t('Parameter Editor') }}</span>
      </div>
      <div class="flex items-center gap-2">
        <button
          v-if="mode === 'form' && !isReadOnly"
          @click="addParameter"
          class="flex items-center gap-1.5 px-3 py-1.5 bg-violet-50 text-violet-600 rounded-lg text-xs font-semibold hover:bg-violet-100 transition-colors"
        >
          <Plus class="size-3.5" />
          {{ t('Add Parameter') }}
        </button>
        <button
          @click="toggleMode"
          class="flex items-center gap-1.5 px-3 py-1.5 bg-gray-50 text-gray-600 rounded-lg text-xs font-semibold hover:bg-gray-100 transition-colors"
        >
          <Code2 v-if="mode === 'form'" class="size-3.5" />
          <LayoutList v-else class="size-3.5" />
          {{ mode === 'form' ? t('Text Mode') : t('Form Mode') }}
        </button>
      </div>
    </div>

    <!-- Form Mode -->
    <div v-if="mode === 'form'" class="flex-1 overflow-y-auto p-5 space-y-4 bg-[#f8f9fb]">
        <div v-if="paramList.length === 0" class="flex flex-col items-center justify-center py-16 text-gray-400">
          <SlidersHorizontal class="size-10 opacity-30 mb-3" />
          <p class="text-sm">{{ t('No parameters configured') }}</p>
          <button
            v-if="!isReadOnly"
            @click="addParameter"
            class="mt-3 flex items-center gap-1.5 px-4 py-2 bg-violet-600 text-white rounded-lg text-xs font-semibold hover:bg-violet-700 transition-colors"
          >
          <Plus class="size-3.5" />
          {{ t('Add Parameter') }}
        </button>
      </div>

      <!-- Parameter Cards -->
      <div
        v-for="(param, index) in paramList"
        :key="param.key"
        class="bg-white rounded-xl shadow-sm p-5 transition-all hover:ring-1 hover:ring-violet-200"
        :class="param.sensitive ? 'border-l-4 border-pink-400' : ''"
      >
        <div class="flex justify-between items-start mb-3">
          <div class="flex items-center gap-3">
            <div class="p-2 rounded-lg" :class="param.sensitive ? 'bg-pink-50' : 'bg-gray-50'">
              <Lock v-if="param.sensitive" class="size-4 text-pink-500" />
              <User v-else-if="param.type === 'string'" class="size-4 text-gray-500" />
              <Clock v-else-if="param.type === 'integer' || param.type === 'number'" class="size-4 text-gray-500" />
              <Settings2 v-else class="size-4 text-gray-500" />
            </div>
            <div>
              <input
                v-if="!isReadOnly"
                v-model="param.name"
                @input="emitChange"
                class="text-sm font-bold text-gray-900 bg-transparent border-none p-0 focus:ring-0 focus:outline-none w-40"
                :placeholder="t('param_name')"
              />
              <p v-else class="text-sm font-bold text-gray-900">{{ param.name }}</p>
              <div class="flex items-center gap-2 mt-0.5">
                <select
                  v-if="!isReadOnly"
                  v-model="param.type"
                  @change="emitChange"
                  class="text-[10px] font-semibold text-gray-400 uppercase tracking-widest bg-transparent border-none p-0 focus:ring-0 cursor-pointer"
                >
                  <option value="string">String</option>
                  <option value="integer">Integer</option>
                  <option value="number">Number</option>
                  <option value="boolean">Boolean</option>
                </select>
                <span v-else class="text-[10px] font-semibold text-gray-400 uppercase tracking-widest">{{ param.type }}</span>
              </div>
            </div>
          </div>
          <div v-if="!isReadOnly" class="flex items-center gap-2">
            <button
              @click="param.sensitive = !param.sensitive; emitChange()"
              class="px-2 py-0.5 text-[10px] font-bold rounded-full uppercase transition-colors"
              :class="param.sensitive
                ? 'bg-pink-100 text-pink-600 hover:bg-pink-200'
                : 'bg-green-100 text-green-600 hover:bg-green-200'"
            >
              {{ param.sensitive ? t('Sensitive') : t('Public') }}
            </button>
            <button
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
              class="w-full bg-gray-50 border-none rounded-lg px-4 py-2.5 text-sm font-medium focus:ring-2 focus:ring-violet-200 transition-all"
              :type="param.sensitive && !param.showPassword ? 'password' : 'text'"
              :placeholder="param.sensitive ? '********' : t('Enter value...')"
            />
            <div v-else class="w-full bg-gray-50 rounded-lg px-4 py-2.5 text-sm font-medium text-gray-700">
              {{ param.sensitive ? '********' : (param.original_value || '-') }}
            </div>
            <button
              v-if="param.sensitive && !isReadOnly"
              @click="param.showPassword = !param.showPassword"
              class="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-violet-600 transition-colors"
            >
              <EyeOff v-if="param.showPassword" class="size-4" />
              <Eye v-else class="size-4" />
            </button>
          </div>
          <button
            v-if="param.sensitive && !isReadOnly"
            @click="openVaultPicker(index)"
            class="flex items-center gap-1.5 px-3 bg-white border-2 border-violet-100 rounded-lg text-xs font-semibold text-violet-600 hover:bg-violet-50 transition-all whitespace-nowrap"
          >
            <KeyRound class="size-3.5" />
            {{ t('Link Vault') }}
          </button>
        </div>

        <!-- Credential Link Display -->
        <div v-if="param.credential_id" class="mt-2 flex items-center gap-1.5">
          <Link2 class="size-3 text-gray-400" />
          <span class="text-[11px] font-semibold text-gray-500">
            {{ t('Linked to') }}
            <span class="text-violet-600">{{ param.credential_id }}</span>
          </span>
          <button v-if="!isReadOnly" @click="param.credential_id = ''; emitChange()" class="ml-1 text-gray-400 hover:text-red-500">
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
            class="rounded border-gray-300 text-violet-600 focus:ring-violet-200 size-3.5"
          />
          <span
            v-else
            class="inline-flex size-3.5 items-center justify-center rounded border text-[9px]"
            :class="param.required ? 'border-violet-300 bg-violet-50 text-violet-600' : 'border-gray-200 bg-gray-50 text-gray-300'"
          >
            {{ param.required ? 'Y' : '' }}
          </span>
          <label :for="`req-${index}`" class="text-xs text-gray-500">{{ t('Required') }}</label>
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
