<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { Shield, Plus, Trash2, Edit3, X } from 'lucide-vue-next';
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
const showForm = ref(false);
const editingId = ref<string | null>(null);

const form = ref<CredentialCreate>({
  name: '',
  username: '',
  password: '',
  domain: '',
});

const resetForm = () => {
  form.value = { name: '', username: '', password: '', domain: '' };
  editingId.value = null;
  showForm.value = false;
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
  if (editingId.value) {
    await updateCredential(editingId.value, {
      name: form.value.name,
      username: form.value.username,
      password: form.value.password || undefined,
      domain: form.value.domain,
    });
  } else {
    if (!form.value.password) return;
    await createCredential(form.value);
  }
  resetForm();
  await load();
};

const startEdit = (cred: Credential) => {
  editingId.value = cred.id;
  form.value = {
    name: cred.name,
    username: cred.username,
    password: '',
    domain: cred.domain,
  };
  showForm.value = true;
};

const remove = async (id: string) => {
  if (!confirm(t('Delete credential confirm'))) return;
  await deleteCredential(id);
  await load();
};

onMounted(load);
</script>

<template>
  <div class="min-h-screen bg-[#f5f6f7]">
    <header class="h-16 bg-white border-b border-gray-200 flex items-center px-8 gap-4">
      <Shield class="text-[#831bd7]" :size="24" />
      <h1 class="text-gray-900 font-extrabold text-xl">{{ t('Credential Management') }}</h1>
      <div class="flex-1"></div>
      <button
        @click="showForm = true; editingId = null; form = { name: '', username: '', password: '', domain: '' }"
        class="flex items-center gap-2 bg-[#831bd7] text-white px-4 py-2 rounded-lg text-sm font-bold hover:bg-[#7018b8]"
      >
        <Plus :size="16" />
        {{ t('New Credential') }}
      </button>
    </header>

    <div class="max-w-4xl mx-auto p-8 space-y-6">
      <!-- Form -->
      <div v-if="showForm" class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
        <div class="flex justify-between items-center mb-4">
          <h2 class="font-bold text-lg">{{ editingId ? t('Edit Credential') : t('New Credential') }}</h2>
          <button @click="resetForm" class="p-1 hover:bg-gray-100 rounded"><X :size="18" /></button>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-xs text-gray-500 font-medium mb-1 block">{{ t('Credential Name') }}</label>
            <input v-model="form.name" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-[#831bd7] outline-none" :placeholder="t('Credential Name')" />
          </div>
          <div>
            <label class="text-xs text-gray-500 font-medium mb-1 block">{{ t('Username') }}</label>
            <input v-model="form.username" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-[#831bd7] outline-none" :placeholder="t('Username')" />
          </div>
          <div>
            <label class="text-xs text-gray-500 font-medium mb-1 block">{{ t('Password') }}</label>
            <input v-model="form.password" type="password" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-[#831bd7] outline-none" :placeholder="editingId ? t('Leave empty to keep') : t('Password')" />
          </div>
          <div>
            <label class="text-xs text-gray-500 font-medium mb-1 block">{{ t('Domain') }}</label>
            <input v-model="form.domain" class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-[#831bd7] outline-none" placeholder="github.com" />
          </div>
        </div>
        <div class="flex justify-end mt-4">
          <button @click="save" class="bg-[#831bd7] text-white px-6 py-2 rounded-lg text-sm font-bold hover:bg-[#7018b8]">
            {{ editingId ? t('Save') : t('Create') }}
          </button>
        </div>
      </div>

      <!-- List -->
      <div class="bg-white rounded-xl p-6 shadow-sm border border-gray-200">
        <div v-if="loading" class="text-center text-gray-400 py-8">{{ t('Loading...') }}</div>
        <div v-else-if="credentials.length === 0" class="text-center text-gray-400 py-8">
          {{ t('No credentials yet') }}
        </div>
        <div v-else class="space-y-3">
          <div
            v-for="cred in credentials"
            :key="cred.id"
            class="flex items-center gap-4 p-4 bg-gray-50 rounded-lg"
          >
            <Shield class="text-[#831bd7] flex-shrink-0" :size="18" />
            <div class="flex-1 min-w-0">
              <p class="text-sm font-semibold text-gray-900">{{ cred.name }}</p>
              <p class="text-xs text-gray-500">{{ cred.username }} {{ cred.domain ? `· ${cred.domain}` : '' }}</p>
            </div>
            <span class="text-xs text-gray-400 font-mono">*****</span>
            <button @click="startEdit(cred)" class="p-1.5 hover:bg-gray-200 rounded" title="编辑">
              <Edit3 :size="14" class="text-gray-500" />
            </button>
            <button @click="remove(cred.id)" class="p-1.5 hover:bg-red-50 rounded" title="删除">
              <Trash2 :size="14" class="text-red-500" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
