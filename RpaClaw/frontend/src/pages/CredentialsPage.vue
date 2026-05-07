<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { Plus, X, KeyRound, Globe, Pencil, Trash2, Clock, Eye, EyeOff } from 'lucide-vue-next';
import {
  listCredentials,
  createCredential,
  updateCredential,
  deleteCredential,
  type Credential,
  type CredentialCreate,
} from '@/api/credential';

const { t } = useI18n();

const credentials = ref<Credential[]>([]);
const loading = ref(true);
const showModal = ref(false);
const editingId = ref<string | null>(null);
const filterText = ref('');
const showPassword = ref(false);
const credentialKind = ref<'basic' | 'model_auth'>('basic');
const modelAuthJson = ref('');
const modelAuthJsonError = ref('');

const defaultModelAuthJson = `{
  "type": "static_headers",
  "config": {
    "headers": {
      "Authorization": "Bearer {{ api_key }}"
    },
    "query": {}
  },
  "variables": {
    "api_key": {
      "sensitive": true,
      "value": ""
    }
  }
}`;

const form = ref<CredentialCreate>({
  kind: 'basic',
  name: '',
  description: '',
  username: '',
  password: '',
  domain: '',
});

const filteredCredentials = computed(() => {
  if (!filterText.value) return credentials.value;
  const q = filterText.value.toLowerCase();
  return credentials.value.filter(
    c => c.name.toLowerCase().includes(q) || c.username.toLowerCase().includes(q) || (c.domain || '').toLowerCase().includes(q)
  );
});

const totalCount = computed(() => credentials.value.length);

const lastUpdated = computed(() => {
  if (!credentials.value.length) return '-';
  const latest = credentials.value.reduce((a, b) =>
    new Date(a.updated_at) > new Date(b.updated_at) ? a : b
  );
  const diff = Date.now() - new Date(latest.updated_at).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return t('Just now');
  if (mins < 60) return `${mins} ${t('minutes ago')}`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} ${t('hours ago')}`;
  const days = Math.floor(hours / 24);
  return `${days} ${t('days ago')}`;
});

const resetForm = () => {
  credentialKind.value = 'basic';
  form.value = { kind: 'basic', name: '', description: '', username: '', password: '', domain: '' };
  modelAuthJson.value = defaultModelAuthJson;
  modelAuthJsonError.value = '';
  editingId.value = null;
  showModal.value = false;
  showPassword.value = false;
};

const openCreate = () => {
  resetForm();
  showModal.value = true;
};

const load = async () => {
  loading.value = true;
  try {
    credentials.value = await listCredentials();
  } finally {
    loading.value = false;
  }
};

const save = async () => {
  if (!form.value.name) return;
  let modelAuthPayload = null;
  if (credentialKind.value === 'model_auth') {
    try {
      modelAuthPayload = JSON.parse(modelAuthJson.value || '{}');
      if (!modelAuthPayload || typeof modelAuthPayload !== 'object' || Array.isArray(modelAuthPayload)) {
        throw new Error('invalid');
      }
      modelAuthJsonError.value = '';
    } catch {
      modelAuthJsonError.value = t('Dynamic Token JSON invalid');
      return;
    }
  }
  if (editingId.value) {
    await updateCredential(editingId.value, {
      name: form.value.name,
      description: form.value.description,
      username: form.value.username,
      password: form.value.password || undefined,
      domain: form.value.domain,
      model_auth: credentialKind.value === 'model_auth' ? modelAuthPayload : undefined,
    });
  } else {
    if (credentialKind.value === 'basic' && !form.value.password) return;
    await createCredential({
      ...form.value,
      kind: credentialKind.value,
      model_auth: credentialKind.value === 'model_auth' ? modelAuthPayload : undefined,
    });
  }
  resetForm();
  await load();
};

const startEdit = (cred: Credential) => {
  editingId.value = cred.id;
  credentialKind.value = cred.kind || 'basic';
  form.value = {
    kind: cred.kind || 'basic',
    name: cred.name,
    description: cred.description || '',
    username: cred.username,
    password: '',
    domain: cred.domain,
  };
  modelAuthJson.value = JSON.stringify(cred.model_auth || JSON.parse(defaultModelAuthJson), null, 2);
  modelAuthJsonError.value = '';
  showModal.value = true;
};

const remove = async (id: string) => {
  if (!confirm(t('Delete credential confirm'))) return;
  await deleteCredential(id);
  await load();
};

onMounted(load);
</script>

<template>
  <div class="h-full w-full overflow-y-auto bg-[#f8f9fb] dark:bg-[#111]">
    <div class="p-10 max-w-full 2xl:max-w-screen-2xl mx-auto">
      <!-- Page Header -->
      <div class="flex flex-col md:flex-row md:items-center justify-between mb-10 gap-6">
        <div>
          <h2 class="text-4xl font-extrabold text-gray-900 dark:text-gray-100 tracking-tight">{{ t('Credential Management') }}</h2>
          <p class="mt-2 text-gray-500 dark:text-gray-400 text-base max-w-2xl">{{ t('Credential page description') }}</p>
        </div>
        <button
          @click="openCreate"
          class="flex items-center gap-2 bg-[#831bd7] text-white px-8 py-3.5 rounded-xl font-bold shadow-xl shadow-purple-500/20 hover:shadow-2xl hover:bg-[#831bd7]/90 transition-all"
        >
          <Plus :size="20" />
          <span>{{ t('New Credential') }}</span>
        </button>
      </div>

      <!-- Stats Cards -->
      <div class="grid grid-cols-1 gap-6 mb-8 md:grid-cols-2">
        <div class="bg-white dark:bg-[#1e1e1e] p-6 rounded-xl shadow-sm border border-transparent dark:border-gray-800 flex items-center gap-5 transition-colors">
          <div class="w-14 h-14 rounded-xl bg-[#831bd7]/5 flex items-center justify-center text-[#831bd7]">
            <KeyRound :size="24" />
          </div>
          <div>
            <p class="text-sm font-semibold text-slate-400 dark:text-slate-500 mb-0.5 uppercase tracking-wider">{{ t('Total credentials') }}</p>
            <p class="text-3xl font-extrabold text-gray-900 dark:text-gray-100">{{ totalCount }}</p>
          </div>
        </div>
        <div class="bg-white dark:bg-[#1e1e1e] p-6 rounded-xl shadow-sm border border-transparent dark:border-gray-800 flex items-center gap-5 transition-colors">
          <div class="w-14 h-14 rounded-xl bg-[#ac0089]/5 flex items-center justify-center text-[#ac0089]">
            <Clock :size="24" />
          </div>
          <div>
            <p class="text-sm font-semibold text-slate-400 dark:text-slate-500 mb-0.5 uppercase tracking-wider">{{ t('Last updated') }}</p>
            <p class="text-3xl font-extrabold text-gray-900 dark:text-gray-100">{{ lastUpdated }}</p>
          </div>
        </div>
      </div>

      <!-- Credentials Table -->
      <div class="bg-white dark:bg-[#1e1e1e] rounded-xl shadow-sm border border-slate-100 dark:border-gray-800 overflow-hidden transition-colors">
        <div class="px-8 py-6 flex flex-col sm:flex-row items-center justify-between gap-4 border-b border-slate-50 dark:border-gray-800">
          <h3 class="text-xl font-bold text-gray-900 dark:text-gray-100">{{ t('Credential list') }}</h3>
          <div class="relative w-full sm:w-auto">
            <input
              v-model="filterText"
              class="bg-slate-50 dark:bg-[#2a2a2a] border border-slate-100 dark:border-gray-700 rounded-xl pl-4 pr-4 py-2 text-sm w-full sm:w-80 focus:ring-2 focus:ring-[#831bd7]/20 outline-none text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 transition-all"
              :placeholder="t('Quick filter...')"
            />
          </div>
        </div>

        <!-- Loading -->
        <div v-if="loading" class="flex items-center justify-center py-20 text-slate-400 dark:text-slate-500">
          {{ t('Loading...') }}
        </div>

        <!-- Empty State -->
        <div v-else-if="credentials.length === 0" class="flex flex-col items-center justify-center py-24">
          <div class="w-16 h-16 rounded-full bg-[#831bd7]/5 flex items-center justify-center mb-6">
            <KeyRound :size="28" class="text-[#831bd7]/40 dark:text-[#831bd7]/60" />
          </div>
          <h4 class="text-xl font-bold text-gray-900 dark:text-gray-100 mb-2">{{ t('No credentials yet') }}</h4>
          <p class="text-slate-400 dark:text-slate-500 mb-8">{{ t('No credentials hint') }}</p>
          <button
            @click="openCreate"
            class="border-2 border-[#831bd7] text-[#831bd7] px-8 py-3 rounded-full font-bold hover:bg-[#831bd7] hover:text-white transition-all"
          >
            {{ t('New Credential') }}
          </button>
        </div>

        <!-- Table -->
        <div v-else class="overflow-x-auto">
          <table class="w-full text-left">
            <thead>
              <tr class="bg-slate-50/50 dark:bg-white/5">
                <th class="px-8 py-4 text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-[0.1em]">{{ t('Credential Name') }}</th>
                <th class="px-8 py-4 text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-[0.1em]">Type</th>
                <th class="px-8 py-4 text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-[0.1em]">{{ t('Username') }}</th>
                <th class="px-8 py-4 text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-[0.1em]">{{ t('Domain') }}</th>
                <th class="px-8 py-4 text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-[0.1em] text-right">{{ t('Actions') }}</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-50 dark:divide-gray-800">
              <tr
                v-for="cred in filteredCredentials"
                :key="cred.id"
                class="group hover:bg-slate-50/50 dark:hover:bg-white/5 transition-colors"
              >
                <td class="px-8 py-5">
                  <div class="flex items-center gap-3">
                    <div class="w-9 h-9 rounded-lg bg-[#831bd7]/5 flex items-center justify-center text-[#831bd7] group-hover:bg-[#831bd7]/10 transition-colors">
                      <Globe :size="16" />
                    </div>
                    <div>
                      <div class="font-bold text-gray-900 dark:text-gray-100">{{ cred.name }}</div>
                      <div v-if="cred.description" class="mt-0.5 text-xs text-slate-400">{{ cred.description }}</div>
                    </div>
                  </div>
                </td>
                <td class="px-8 py-5 text-gray-500 dark:text-gray-400 font-medium">
                  {{ cred.kind === 'model_auth' ? 'Model Auth' : 'Basic' }}
                </td>
                <td class="px-8 py-5 text-gray-500 dark:text-gray-400 font-medium">{{ cred.username }}</td>
                <td class="px-8 py-5 text-slate-400 dark:text-slate-500 italic">{{ cred.domain || '-' }}</td>
                <td class="px-8 py-5 text-right">
                  <div class="flex items-center justify-end gap-2">
                    <button @click="startEdit(cred)" class="p-2 text-slate-400 dark:text-slate-500 hover:text-[#831bd7] dark:hover:text-[#831bd7] transition-colors" :title="t('Edit')">
                      <Pencil :size="16" />
                    </button>
                    <button @click="remove(cred.id)" class="p-2 text-slate-400 dark:text-slate-500 hover:text-red-500 transition-colors" :title="t('Delete')">
                      <Trash2 :size="16" />
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Modal Overlay -->
    <Teleport to="body">
      <div v-if="showModal" class="fixed inset-0 z-[60] bg-slate-900/40 dark:bg-black/60 backdrop-blur-sm flex items-center justify-center p-6" @click.self="resetForm">
        <div class="bg-white dark:bg-[#1e1e1e] w-full max-w-xl rounded-2xl shadow-2xl overflow-hidden border border-transparent dark:border-gray-800">
          <div class="p-8 border-b border-slate-100 dark:border-gray-800 flex items-center justify-between">
            <h3 class="text-2xl font-extrabold text-gray-900 dark:text-gray-100">{{ editingId ? t('Edit Credential') : t('New Credential') }}</h3>
            <button @click="resetForm" class="p-2 text-gray-500 dark:text-gray-400 hover:bg-slate-100 dark:hover:bg-white/10 rounded-full transition-all">
              <X :size="20" />
            </button>
          </div>
          <div class="p-8 space-y-6">
            <div>
              <label class="block text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">Type</label>
              <select
                v-model="credentialKind"
                :disabled="!!editingId"
                class="w-full bg-[#eff1f2] dark:bg-[#2a2a2a] border-none rounded-lg p-3 text-sm focus:ring-2 focus:ring-[#831bd7]/20 outline-none text-gray-900 dark:text-gray-100 transition-colors disabled:opacity-60"
              >
                <option value="basic">Basic Credential</option>
                <option value="model_auth">Model Auth</option>
              </select>
            </div>
            <div>
              <label class="block text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">{{ t('Credential Name') }}</label>
              <input
                v-model="form.name"
                class="w-full bg-[#eff1f2] dark:bg-[#2a2a2a] border-none rounded-lg p-3 text-sm focus:ring-2 focus:ring-[#831bd7]/20 outline-none text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 transition-colors"
                :placeholder="t('Credential name placeholder')"
              />
            </div>
            <div v-if="credentialKind === 'model_auth'">
              <label class="block text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">Description</label>
              <input
                v-model="form.description"
                class="w-full bg-[#eff1f2] dark:bg-[#2a2a2a] border-none rounded-lg p-3 text-sm focus:ring-2 focus:ring-[#831bd7]/20 outline-none text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 transition-colors"
              />
            </div>
            <div v-if="credentialKind === 'basic'" class="grid grid-cols-2 gap-4">
              <div>
                <label class="block text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">{{ t('Username') }}</label>
                <input
                  v-model="form.username"
                  class="w-full bg-[#eff1f2] dark:bg-[#2a2a2a] border-none rounded-lg p-3 text-sm focus:ring-2 focus:ring-[#831bd7]/20 outline-none text-gray-900 dark:text-gray-100 transition-colors"
                />
              </div>
              <div>
                <label class="block text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">{{ t('Domain') }}</label>
                <input
                  v-model="form.domain"
                  class="w-full bg-[#eff1f2] dark:bg-[#2a2a2a] border-none rounded-lg p-3 text-sm focus:ring-2 focus:ring-[#831bd7]/20 outline-none text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 transition-colors"
                  placeholder="example.com"
                />
              </div>
            </div>
            <div v-if="credentialKind === 'basic'">
              <label class="block text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">{{ t('Password') }} / Token</label>
              <div class="relative">
                <input
                  v-model="form.password"
                  :type="showPassword ? 'text' : 'password'"
                  class="w-full bg-[#eff1f2] dark:bg-[#2a2a2a] border-none rounded-lg p-3 pr-10 text-sm focus:ring-2 focus:ring-[#831bd7]/20 outline-none text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 transition-colors"
                  :placeholder="editingId ? t('Leave empty to keep') : ''"
                />
                <button
                  type="button"
                  @click="showPassword = !showPassword"
                  class="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
                >
                  <EyeOff v-if="showPassword" :size="16" />
                  <Eye v-else :size="16" />
                </button>
              </div>
            </div>
            <div v-else>
              <label class="block text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">Model Auth JSON</label>
              <textarea
                v-model="modelAuthJson"
                rows="14"
                class="w-full min-h-[320px] bg-[#eff1f2] dark:bg-[#2a2a2a] border-none rounded-lg p-3 font-mono text-xs focus:ring-2 focus:ring-[#831bd7]/20 outline-none text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 transition-colors"
              ></textarea>
              <p v-if="modelAuthJsonError" class="mt-2 text-xs text-red-500">{{ modelAuthJsonError }}</p>
            </div>
          </div>
          <div class="px-8 py-6 bg-slate-50/50 dark:bg-black/20 border-t border-slate-100 dark:border-gray-800 flex justify-end gap-4 transition-colors">
            <button @click="resetForm" class="px-6 py-3 font-bold text-slate-500 dark:text-slate-400 hover:text-gray-900 dark:hover:text-gray-100 transition-colors">
              {{ t('Cancel') }}
            </button>
            <button
              @click="save"
              class="bg-[#831bd7] text-white px-8 py-3 rounded-full font-bold shadow-lg shadow-purple-500/20 hover:opacity-90 transition-all"
            >
              {{ editingId ? t('Save') : t('Create') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
