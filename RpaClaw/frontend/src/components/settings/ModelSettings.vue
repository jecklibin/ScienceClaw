<template>
  <div class="flex flex-col gap-6 py-2 px-1">
    <div v-if="loading" class="flex justify-center py-12">
      <Loader2 class="size-8 animate-spin text-gray-300" />
    </div>

    <div v-else class="flex flex-col gap-8">
      <!-- System Models Section -->
      <div v-if="systemModels.length > 0" class="flex flex-col gap-4">
        <h3 class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider px-1 flex items-center gap-2">
            {{ t('System Models') }}
            <span class="h-px flex-1 bg-gradient-to-r from-gray-200 dark:from-gray-700 to-transparent"></span>
        </h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div
            v-for="model in systemModels"
            :key="model.id"
            class="group relative flex flex-col bg-white dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700/50 rounded-2xl shadow-sm hover:border-blue-200 dark:hover:border-blue-700/50 hover:shadow-md transition-all duration-300 overflow-hidden"
            >
                <!-- Header with Status -->
                <div class="absolute top-0 right-0 p-3">
                    <div class="flex items-center gap-1.5 px-2.5 py-1 bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 rounded-full border border-emerald-100 dark:border-emerald-800/40 shadow-sm">
                        <CheckCircle2 class="size-3.5" />
                        <span class="text-[10px] font-bold uppercase tracking-wide">{{ t('Verified') }}</span>
                    </div>
                </div>

                <!-- Main Content -->
                <div class="p-5 flex flex-col gap-4">
                    <!-- Title Area -->
                    <div class="flex items-start gap-4">
                        <div class="size-12 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center text-white shadow-md shadow-blue-500/20">
                            <ShieldCheck class="size-6" />
                        </div>
                        <div class="flex flex-col pt-0.5">
                            <h4 class="font-bold text-gray-800 dark:text-gray-100 text-lg leading-tight">{{ model.name }}</h4>
                            <span class="text-xs font-medium text-gray-400 dark:text-gray-500 mt-1">
                                {{ t('System Built-in') }}
                            </span>
                        </div>
                    </div>

                    <!-- Info Grid -->
                    <div class="grid grid-cols-1 gap-2 bg-[var(--background-gray-main)] rounded-lg p-3 border border-[var(--border-light)]">
                        <!-- Provider -->
                        <div class="flex items-center justify-between text-xs">
                            <span class="text-[var(--text-tertiary)] flex items-center gap-1.5 whitespace-nowrap">
                                <Box class="size-3.5" />
                                {{ t('Provider') }}
                            </span>
                            <span class="font-semibold text-[var(--text-secondary)] capitalize">{{ model.provider }}</span>
                        </div>
                        
                        <!-- Model ID -->
                        <div class="flex items-center justify-between text-xs border-t border-[var(--border-light)] border-dashed pt-2">
                            <span class="text-[var(--text-tertiary)] flex items-center gap-1.5 whitespace-nowrap">
                                <Box class="size-3.5" />
                                {{ t('Model ID') }}
                            </span>
                            <span class="font-mono text-[var(--text-primary)] bg-white px-1.5 rounded border border-[var(--border-light)]">{{ model.model_name }}</span>
                        </div>

                        <!-- Base URL -->
                        <div class="flex items-center justify-between text-xs border-t border-[var(--border-light)] border-dashed pt-2">
                            <span class="text-[var(--text-tertiary)] flex items-center gap-1.5 whitespace-nowrap">
                                <Globe class="size-3.5" />
                                {{ t('Base URL') }}
                            </span>
                            <div class="flex-1 flex justify-end ml-4 overflow-hidden">
                                <span v-if="!model.base_url" class="px-1.5 py-0.5 rounded bg-gray-100 text-[var(--text-tertiary)] text-[10px] font-medium border border-[var(--border-light)] whitespace-nowrap">
                                    {{ t('Default Endpoint') }}
                                </span>
                                <span v-else class="font-mono text-[var(--text-tertiary)] truncate" :title="model.base_url">
                                    {{ model.base_url }}
                                </span>
                            </div>
                        </div>

                         <!-- API Key -->
                        <div class="flex items-center justify-between text-xs border-t border-[var(--border-light)] border-dashed pt-2">
                            <span class="text-[var(--text-tertiary)] flex items-center gap-1.5 whitespace-nowrap">
                                <Key class="size-3.5" />
                                {{ t('API Key') }}
                            </span>
                            <div class="flex items-center gap-1.5">
                                <span class="size-2 rounded-full bg-green-500 animate-pulse"></span>
                                <span class="font-mono text-[var(--text-tertiary)] text-[10px]">********</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
      </div>

      <!-- User Models Section -->
      <div class="flex flex-col gap-4">
        <div class="flex justify-between items-center px-1">
             <h3 class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider flex items-center gap-2 flex-1">
                {{ t('Custom Models') }}
                <span class="h-px flex-1 bg-gradient-to-r from-gray-200 dark:from-gray-700 to-transparent mr-4"></span>
             </h3>
             <button
                class="flex items-center gap-1.5 px-4 py-2 text-white bg-gradient-to-r from-blue-500 to-indigo-600 rounded-xl text-xs font-semibold shadow-md shadow-blue-500/20 hover:shadow-lg hover:shadow-blue-500/30 active:scale-[0.97] transition-all duration-200 group"
                @click="openEditModal(null)"
            >
                <Plus class="size-3.5" />
                {{ t('Add Model') }}
            </button>
        </div>

        <div v-if="userModels.length === 0" class="flex flex-col items-center justify-center py-12 bg-gray-50/80 dark:bg-gray-800/30 rounded-2xl border-2 border-dashed border-gray-200 dark:border-gray-700">
            <div class="size-14 rounded-2xl bg-gradient-to-br from-gray-100 to-gray-50 dark:from-gray-700 dark:to-gray-800 flex items-center justify-center mb-3">
                <Box class="size-6 text-gray-300 dark:text-gray-500" />
            </div>
            <p class="font-semibold text-gray-400 dark:text-gray-500">{{ t('No custom models configured') }}</p>
            <p class="text-xs mt-1 text-gray-300 dark:text-gray-600">{{ t('Add your own OpenAI, Anthropic or other compatible models.') }}</p>
        </div>
        
        <div v-else class="grid grid-cols-1 gap-3">
             <div 
                v-for="model in userModels" 
                :key="model.id"
                class="group flex items-center justify-between p-4 bg-[var(--background-white-main)] border border-[var(--border-light)] rounded-xl hover:border-[var(--border-main)] hover:shadow-md transition-all duration-200"
            >
                <div class="flex flex-col gap-1.5">
                    <div class="flex items-center gap-3">
                        <div class="size-8 rounded-lg bg-gray-50 border border-gray-100 flex items-center justify-center text-gray-500">
                             <Box class="size-4" />
                        </div>
                        <div class="flex flex-col">
                            <span class="font-semibold text-[var(--text-primary)] text-sm">{{ model.name }}</span>
                            <div class="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                                <span class="capitalize">{{ model.provider }}</span>
                                <span class="text-[var(--text-disable)]">•</span>
                                <span class="font-mono">{{ model.model_name }}</span>
                                <span v-if="model.auth_config?.type" class="text-[var(--text-disable)]">•</span>
                                <span v-if="model.auth_config?.type === 'static_headers'" class="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-500/10 dark:text-emerald-300">
                                    <Key class="size-3" />
                                    {{ t('Static Token') }}
                                </span>
                                <span v-if="model.auth_config?.type === 'dynamic_token'" class="inline-flex items-center gap-1 rounded-md border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[10px] font-semibold text-violet-700 dark:border-violet-400/20 dark:bg-violet-500/10 dark:text-violet-300">
                                    <Key class="size-3" />
                                    {{ t('Dynamic Token') }}
                                </span>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
                    <button 
                        class="p-2 text-[var(--text-secondary)] hover:text-[var(--icon-primary)] hover:bg-[var(--background-gray-main)] rounded-lg transition-all"
                        :title="t('Edit')"
                        @click="openEditModal(model)"
                    >
                        <Pencil class="size-4" />
                    </button>
                    <button 
                        class="p-2 text-[var(--text-secondary)] hover:text-red-600 hover:bg-red-50 rounded-lg transition-all"
                        :title="t('Delete')"
                        @click="confirmDelete(model)"
                    >
                        <Trash2 class="size-4" />
                    </button>
                </div>
            </div>
        </div>
      </div>
    </div>

    <!-- Edit/Add Dialog -->
    <Dialog v-model:open="isEditOpen">
      <DialogContent class="w-[calc(100vw-16px)] max-w-[780px] max-h-[92vh] p-0 overflow-hidden bg-white/95 dark:bg-gray-900/95 backdrop-blur-xl rounded-2xl shadow-2xl border border-gray-200/60 dark:border-gray-700/40 flex flex-col">
        <DialogHeader class="px-6 py-4 border-b border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/30 flex flex-row items-center justify-between">
          <DialogTitle class="text-lg font-bold text-gray-800 dark:text-gray-100 flex items-center gap-2">
            <div class="size-8 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center">
              <Box class="size-4 text-white" />
            </div>
            {{ editingModel ? t('Edit Model') : t('Add Model') }}
          </DialogTitle>
        </DialogHeader>
        
        <div class="flex-1 overflow-y-auto px-4 py-4 sm:px-6 sm:py-6">
          <div class="flex flex-col gap-5">
            <!-- Provider Selection -->
             <div class="grid gap-2">
                <label class="text-sm font-medium text-[var(--text-secondary)]">{{ t('Provider') }} <span class="text-red-500">*</span></label>
                <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
                    <button 
                        v-for="p in ['openai', 'anthropic', 'deepseek', 'gemini', 'glm', 'qwen', 'kimi', 'minimax', 'other']" 
                        :key="p"
                        type="button"
                        class="px-3 py-2 rounded-lg text-xs font-medium border transition-all capitalize flex items-center justify-center gap-1.5"
                        :class="form.provider === p ? 'bg-blue-50 border-blue-200 text-blue-700 ring-1 ring-blue-200' : 'bg-[var(--background-white-main)] border-[var(--border-main)] text-[var(--text-secondary)] hover:bg-[var(--fill-tsp-gray-main)]'"
                        @click="selectProvider(p)"
                    >
                        <ProviderIcon :provider="p" class="size-4" />
                        {{ p }}
                    </button>
                </div>
            </div>

            <!-- Display Name & Model Name -->
            <div class="grid grid-cols-1 sm:grid-cols-[2fr_3fr] gap-4">
                 <div class="grid gap-2">
                    <label class="text-sm font-medium text-[var(--text-secondary)]">{{ t('Display Name') }} <span class="text-red-500">*</span></label>
                    <input 
                    v-model="form.name" 
                    class="flex h-9 w-full rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)] disabled:cursor-not-allowed disabled:opacity-50"
                    placeholder="e.g. My GPT-4"
                    />
                </div>
                <div class="grid gap-2">
                    <label class="text-sm font-medium text-[var(--text-secondary)]">{{ t('Model ID') }} <span class="text-red-500">*</span></label>
                    <div v-if="form.provider !== 'other' && providerModels.length > 0" class="relative" ref="dropdownRef">
                        <button
                            type="button"
                            class="flex h-9 w-full items-center justify-between rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                            @click="modelDropdownOpen = !modelDropdownOpen"
                        >
                            <span :class="form.model_name ? 'text-[var(--text-primary)]' : 'text-[var(--text-disable)]'">
                                {{ form.model_name || t('Select a model') }}
                            </span>
                            <ChevronDown class="size-4 text-gray-400 shrink-0 transition-transform" :class="modelDropdownOpen && 'rotate-180'" />
                        </button>
                        <div v-if="modelDropdownOpen" class="absolute z-50 mt-1 w-full rounded-lg border border-[var(--border-main)] bg-white dark:bg-gray-800 shadow-lg overflow-hidden">
                            <div class="max-h-48 overflow-y-auto py-1">
                                <button
                                    v-for="m in providerModels"
                                    :key="m"
                                    type="button"
                                    class="flex w-full items-center px-3 py-2 text-sm hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-colors"
                                    :class="form.model_name === m ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 font-medium' : 'text-[var(--text-primary)]'"
                                    @click="selectModel(m)"
                                >
                                    {{ m }}
                                </button>
                            </div>
                            <div class="border-t border-[var(--border-light)] p-2">
                                <input
                                    v-model="customModelInput"
                                    class="flex h-8 w-full rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 py-1 text-xs shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                    :placeholder="t('Enter other model')"
                                    @keydown.enter.prevent="applyCustomModel"
                                />
                            </div>
                        </div>
                    </div>
                    <input
                    v-else
                    v-model="form.model_name"
                    @input="onModelSelect"
                    class="flex h-9 w-full rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)] disabled:cursor-not-allowed disabled:opacity-50"
                    placeholder="e.g. gpt-4-turbo"
                    />
                </div>
            </div>

            <!-- Base URL (hidden for Gemini — uses Google AI SDK directly) -->
            <div v-if="form.provider !== 'gemini'" class="grid gap-2">
                <label class="text-sm font-medium text-[var(--text-secondary)] flex items-center gap-1">
                    {{ t('Base URL') }}
                    <span class="text-[10px] text-[var(--text-tertiary)] font-normal ml-auto" v-if="!form.base_url && form.provider !== 'other'">{{ t('Please fill in manually') }}</span>
                </label>
                <input 
                v-model="form.base_url" 
                class="flex h-9 w-full rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)] disabled:cursor-not-allowed disabled:opacity-50"
                placeholder="https://api.openai.com/v1"
                />
            </div>

            <!-- API Key -->
            <div class="grid gap-2">
                <label class="text-sm font-medium text-[var(--text-secondary)]">{{ t('API Key') }} <span class="text-red-500" v-if="!editingModel">*</span></label>
                <input 
                v-model="form.api_key" 
                type="password"
                class="flex h-9 w-full rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)] disabled:cursor-not-allowed disabled:opacity-50 font-mono"
                :placeholder="editingModel ? t('Leave empty to keep existing key') : 'sk-...'"
                />
            </div>

            <!-- Context Window -->
            <div class="grid gap-2">
                <label class="text-sm font-medium text-[var(--text-secondary)] flex items-center gap-1">
                    {{ t('Context Window') }}
                    <span class="text-[10px] text-[var(--text-tertiary)] font-normal ml-auto">{{ t('Auto-detected if empty') }}</span>
                </label>
                <div class="flex gap-2">
                    <input 
                    v-model.number="form.context_window" 
                    type="number"
                    min="1024"
                    step="1024"
                    class="flex h-9 w-full rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)] disabled:cursor-not-allowed disabled:opacity-50 font-mono"
                    :placeholder="t('e.g. 131072')"
                    />
                    <button
                        type="button"
                        :disabled="detecting || !form.model_name"
                        @click="detectCtxWindow"
                        class="flex-shrink-0 h-9 px-3 rounded-md border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/30 text-blue-600 dark:text-blue-400 text-xs font-medium hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1.5"
                        :title="t('Detect')"
                    >
                        <Loader2 v-if="detecting" class="size-3.5 animate-spin" />
                        <Radar v-else class="size-3.5" />
                        {{ t('Detect') }}
                    </button>
                </div>
            </div>

            <!-- Authentication Config -->
            <div class="grid gap-2">
                <label class="text-sm font-medium text-[var(--text-secondary)] flex items-center gap-1">
                    {{ t('Authentication Config') }}
                    <span class="text-[10px] text-[var(--text-tertiary)] font-normal ml-auto">{{ t('Current Auth Mode') }}: {{ authSummaryText }}</span>
                </label>
                <div v-if="modelAuthCredentials.length" class="grid gap-1">
                    <select
                        v-model="selectedAuthCredentialId"
                        class="flex h-9 w-full rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                    >
                        <option value="">{{ t('Create auth with this model') }}</option>
                        <option v-for="cred in modelAuthCredentials" :key="cred.id" :value="cred.id">
                            {{ cred.name }} · {{ cred.model_auth?.type === 'dynamic_token' ? t('Dynamic Token') : t('Static Token') }}
                        </option>
                    </select>
                    <p v-if="selectedAuthCredential" class="text-[11px] text-[var(--text-tertiary)]">
                        {{ t('Selected model auth will be reused') }}
                    </p>
                </div>
                <div v-if="selectedAuthCredentialId" class="rounded-lg border border-blue-100 bg-blue-50/70 px-3 py-2 text-xs text-blue-800 dark:border-blue-400/20 dark:bg-blue-500/10 dark:text-blue-200">
                    {{ selectedAuthCredential?.description || t('This model will reuse the selected authentication credential.') }}
                </div>
                <div v-else class="grid gap-3">
                <div ref="authDropdownRef" class="relative">
                    <button
                        type="button"
                        class="flex h-9 w-full items-center justify-between gap-3 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 text-left text-sm shadow-sm transition-colors hover:bg-[var(--fill-tsp-gray-main)]"
                        :title="t('Authentication Config')"
                        @click="authOptionsOpen = !authOptionsOpen"
                    >
                        <span class="truncate text-[var(--text-primary)]">{{ authSummaryText }}</span>
                        <ChevronDown class="size-4 shrink-0 text-[var(--text-tertiary)] transition-transform" :class="authOptionsOpen && 'rotate-180'" />
                    </button>

                    <div
                        v-if="authOptionsOpen"
                        class="absolute bottom-[calc(100%+6px)] left-0 right-0 z-50 overflow-hidden rounded-lg border border-[var(--border-main)] bg-[var(--background-white-main)] p-1 shadow-xl"
                    >
                        <button
                            type="button"
                            class="flex h-9 w-full items-center justify-between rounded-md px-3 text-left text-sm font-medium transition-colors hover:bg-[var(--fill-tsp-gray-main)]"
                            :class="authMode === 'none' ? 'bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300' : 'text-[var(--text-secondary)]'"
                            @click="selectAuthMode('none')"
                        >
                            <span>{{ t('No Extra Auth') }}</span>
                            <Check v-if="authMode === 'none'" class="size-4 text-blue-600" />
                        </button>
                        <button
                            type="button"
                            class="flex h-9 w-full items-center justify-between rounded-md px-3 text-left text-sm font-medium transition-colors hover:bg-[var(--fill-tsp-gray-main)]"
                            :class="authMode === 'static_headers' ? 'bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300' : 'text-[var(--text-secondary)]'"
                            @click="selectAuthMode('static_headers')"
                        >
                            <span>{{ t('Static Token') }}</span>
                            <Check v-if="authMode === 'static_headers'" class="size-4 text-blue-600" />
                        </button>
                        <button
                            type="button"
                            class="flex h-9 w-full items-center justify-between rounded-md px-3 text-left text-sm font-medium transition-colors hover:bg-[var(--fill-tsp-gray-main)]"
                            :class="authMode === 'dynamic_token' ? 'bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300' : 'text-[var(--text-secondary)]'"
                            @click="selectAuthMode('dynamic_token')"
                        >
                            <span>{{ t('Dynamic Token') }}</span>
                            <Check v-if="authMode === 'dynamic_token'" class="size-4 text-blue-600" />
                        </button>
                    </div>
                </div>

                <div v-if="authMode === 'static_headers'" class="grid gap-3">
                    <div class="grid gap-1">
                        <label class="text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">{{ t('Header Config') }}</label>
                        <p class="text-[11px] leading-5 text-[var(--text-tertiary)]">
                            {{ form.api_key ? t('API key will be sent as Authorization Bearer') : t('API key empty no default Authorization') }}
                        </p>
                    </div>
                    <div class="overflow-hidden rounded-lg border border-[var(--border-light)] bg-[var(--background-white-main)]">
                        <div class="grid grid-cols-[1fr_1.4fr_40px] gap-2 border-b border-[var(--border-light)] px-3 py-2 text-[11px] font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                            <span>{{ t('Header Name') }}</span>
                            <span>{{ t('Header Value') }}</span>
                            <button
                                type="button"
                                class="flex size-7 items-center justify-center rounded-md text-blue-600 transition-all hover:bg-blue-50"
                                :title="t('Add Header')"
                                @click="addStaticHeader"
                            >
                                <Plus class="size-4" />
                            </button>
                        </div>
                        <div class="grid grid-cols-[1fr_1.4fr_40px] gap-2 border-b border-[var(--border-light)] px-3 py-2">
                            <input
                                value="Authorization"
                                readonly
                                class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-tsp-gray-main)] px-2 text-sm text-[var(--text-secondary)] shadow-sm focus-visible:outline-none"
                            />
                            <input
                                :value="authorizationHeaderValue"
                                @input="updateAuthorizationHeaderValue"
                                type="password"
                                class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 font-mono text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                placeholder="Bearer sk-..."
                            />
                            <div
                                class="flex size-9 items-center justify-center rounded-md text-[var(--text-tertiary)]"
                                :title="t('Fixed Authorization Header')"
                            >
                                <ShieldCheck class="size-4" />
                            </div>
                        </div>
                        <div v-for="(header, index) in staticHeaders" :key="header.localId" class="grid grid-cols-[1fr_1.4fr_40px] gap-2 border-b border-[var(--border-light)] px-3 py-2 last:border-b-0">
                            <input
                                :value="header.name"
                                @input="updateStaticHeaderField(header, 'name', $event)"
                                class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                placeholder="X-Gateway-Token"
                            />
                            <input
                                :value="header.value"
                                @input="updateStaticHeaderField(header, 'value', $event)"
                                type="password"
                                class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 font-mono text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                :placeholder="header.credential_id ? t('Configured - leave blank to keep') : 'static-token'"
                            />
                            <button
                                type="button"
                                class="flex size-9 items-center justify-center rounded-md text-[var(--text-secondary)] transition-all hover:bg-red-50 hover:text-red-600"
                                :title="t('Delete')"
                                @click="removeStaticHeader(index)"
                            >
                                <Trash2 class="size-4" />
                            </button>
                        </div>
                    </div>

                    <div class="grid gap-2">
                        <label class="text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">{{ t('Config JSON') }}</label>
                        <textarea
                            v-model="staticAuthJsonInput"
                            rows="8"
                            class="min-h-[176px] rounded-lg border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 py-2 font-mono text-xs shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                            :class="staticAuthJsonError && 'border-red-300 focus-visible:ring-red-300'"
                            :placeholder="staticAuthJsonPlaceholder"
                            @input="applyStaticAuthJsonInput"
                        ></textarea>
                        <p v-if="staticAuthJsonError" class="text-[11px] text-red-500">{{ staticAuthJsonError }}</p>
                    </div>
                </div>

                <div v-if="authMode === 'dynamic_token'" class="grid gap-4">
                        <div class="grid gap-2 rounded-lg border border-blue-100 bg-blue-50/70 p-3 text-xs text-blue-800 dark:border-blue-400/20 dark:bg-blue-500/10 dark:text-blue-200">
                            <div class="font-semibold">{{ t('Dynamic Token Flow') }}</div>
                            <div class="grid gap-1 leading-5">
                                <p>{{ t('Dynamic Token Flow Request') }}</p>
                                <p>{{ t('Dynamic Token Flow Cache') }}</p>
                                <p>{{ t('Dynamic Token Flow Inject') }}</p>
                            </div>
                        </div>

                        <div class="grid gap-3 rounded-lg border border-[var(--border-light)] bg-[var(--background-white-main)] p-3">
                            <div class="flex items-center justify-between gap-3">
                                <div>
                                    <div class="text-sm font-semibold text-[var(--text-primary)]">{{ t('1. Build Token Request') }}</div>
                                    <div class="text-[11px] text-[var(--text-tertiary)]">{{ t('Build Token Request Hint') }}</div>
                                </div>
                                <div class="flex items-center gap-2">
                                    <button
                                        type="button"
                                        class="inline-flex h-9 items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 text-xs font-semibold text-blue-700 transition-colors hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-blue-400/20 dark:bg-blue-500/10 dark:text-blue-200"
                                        :disabled="testingDynamicToken"
                                        @click="runDynamicTokenTest"
                                    >
                                        <Loader2 v-if="testingDynamicToken" class="size-3.5 animate-spin" />
                                        <span>{{ testingDynamicToken ? t('Testing Token') : t('Test Token Request') }}</span>
                                    </button>
                                    <select
                                        v-model="dynamicTokenRequest.method"
                                        class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                        @change="syncDynamicRequestJsonFromRows"
                                    >
                                        <option value="GET">GET</option>
                                        <option value="POST">POST</option>
                                        <option value="PUT">PUT</option>
                                        <option value="PATCH">PATCH</option>
                                    </select>
                                </div>
                            </div>

                            <div class="grid gap-2">
                                <label class="text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">{{ t('Token URL') }}</label>
                                <input
                                    v-model="dynamicTokenRequest.url"
                                    class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 font-mono text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                    placeholder="https://auth.company.com/token"
                                    @input="syncDynamicRequestJsonFromRows"
                                />
                            </div>

                            <div class="grid gap-3">
                                <div class="overflow-hidden rounded-lg border border-[var(--border-main)] bg-[var(--background-white-main)]">
                                    <div class="grid grid-cols-[1fr_1.6fr_44px] items-center border-b border-[var(--border-light)] px-3 py-2 text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                                        <span>{{ t('Token Request Header Name') }}</span>
                                        <span>{{ t('Token Request Header Value') }}</span>
                                        <button
                                            type="button"
                                            class="flex size-8 items-center justify-center justify-self-end rounded-md text-blue-600 transition-colors hover:bg-blue-50 dark:hover:bg-blue-500/10"
                                            :title="t('Add Parameter')"
                                            @click="addDynamicRequestRow('headers')"
                                        >
                                            <Plus class="size-4" />
                                        </button>
                                    </div>
                                    <div class="grid gap-2 p-3">
                                        <div
                                            v-for="(row, index) in dynamicTokenRequest.headers"
                                            :key="row.localId"
                                            class="grid grid-cols-[1fr_1.6fr_44px] gap-2"
                                        >
                                            <input
                                                :value="row.name"
                                                class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                                placeholder="Content-Type"
                                                @input="updateDynamicRequestRow('headers', index, 'name', $event)"
                                            />
                                            <input
                                                :value="row.value"
                                                class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 font-mono text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                                placeholder="application/json"
                                                @input="updateDynamicRequestRow('headers', index, 'value', $event)"
                                            />
                                            <button
                                                type="button"
                                                class="flex size-9 items-center justify-center rounded-md text-[var(--text-secondary)] transition-all hover:bg-red-50 hover:text-red-600"
                                                :title="t('Delete')"
                                                @click="removeDynamicRequestRow('headers', index)"
                                            >
                                                <Trash2 class="size-4" />
                                            </button>
                                        </div>
                                        <div v-if="!dynamicTokenRequest.headers.length" class="rounded-md border border-dashed border-[var(--border-main)] px-3 py-4 text-center text-xs text-[var(--text-tertiary)]">
                                            {{ t('No parameters configured') }}
                                        </div>
                                    </div>
                                </div>

                                <div class="overflow-hidden rounded-lg border border-[var(--border-main)] bg-[var(--background-white-main)]">
                                    <div class="grid grid-cols-[1fr_1.6fr_44px] items-center border-b border-[var(--border-light)] px-3 py-2 text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                                        <span>{{ t('Token Request Query Name') }}</span>
                                        <span>{{ t('Token Request Query Value') }}</span>
                                        <button
                                            type="button"
                                            class="flex size-8 items-center justify-center justify-self-end rounded-md text-blue-600 transition-colors hover:bg-blue-50 dark:hover:bg-blue-500/10"
                                            :title="t('Add Parameter')"
                                            @click="addDynamicRequestRow('query')"
                                        >
                                            <Plus class="size-4" />
                                        </button>
                                    </div>
                                    <div class="grid gap-2 p-3">
                                        <div
                                            v-for="(row, index) in dynamicTokenRequest.query"
                                            :key="row.localId"
                                            class="grid grid-cols-[1fr_1.6fr_44px] gap-2"
                                        >
                                            <input
                                                :value="row.name"
                                                class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                                placeholder="audience"
                                                @input="updateDynamicRequestRow('query', index, 'name', $event)"
                                            />
                                            <input
                                                :value="row.value"
                                                class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 font-mono text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                                placeholder="model-api"
                                                @input="updateDynamicRequestRow('query', index, 'value', $event)"
                                            />
                                            <button
                                                type="button"
                                                class="flex size-9 items-center justify-center rounded-md text-[var(--text-secondary)] transition-all hover:bg-red-50 hover:text-red-600"
                                                :title="t('Delete')"
                                                @click="removeDynamicRequestRow('query', index)"
                                            >
                                                <Trash2 class="size-4" />
                                            </button>
                                        </div>
                                        <div v-if="!dynamicTokenRequest.query.length" class="rounded-md border border-dashed border-[var(--border-main)] px-3 py-4 text-center text-xs text-[var(--text-tertiary)]">
                                            {{ t('No parameters configured') }}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <div class="overflow-hidden rounded-lg border border-[var(--border-main)] bg-[var(--background-white-main)]">
                                <div class="grid grid-cols-[1fr_1.6fr_44px] items-center border-b border-[var(--border-light)] px-3 py-2 text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                                    <span>{{ t('Token Request Body Name') }}</span>
                                    <span>{{ t('Token Request Body Value') }}</span>
                                    <button
                                        type="button"
                                        class="flex size-8 items-center justify-center justify-self-end rounded-md text-blue-600 transition-colors hover:bg-blue-50 dark:hover:bg-blue-500/10"
                                        :title="t('Add Parameter')"
                                        @click="addDynamicRequestRow('body')"
                                    >
                                        <Plus class="size-4" />
                                    </button>
                                </div>
                                <div class="grid gap-2 p-3">
                                    <div
                                        v-for="(row, index) in dynamicTokenRequest.body"
                                        :key="row.localId"
                                        class="grid grid-cols-[1fr_1.6fr_44px] gap-2"
                                    >
                                        <input
                                            :value="row.name"
                                            class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                            placeholder="client_id"
                                            @input="updateDynamicRequestRow('body', index, 'name', $event)"
                                        />
                                        <input
                                            :value="row.value"
                                            type="password"
                                            class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 font-mono text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                            placeholder="your-client-id"
                                            @input="updateDynamicRequestRow('body', index, 'value', $event)"
                                        />
                                        <button
                                            type="button"
                                            class="flex size-9 items-center justify-center rounded-md text-[var(--text-secondary)] transition-all hover:bg-red-50 hover:text-red-600"
                                            :title="t('Delete')"
                                            @click="removeDynamicRequestRow('body', index)"
                                        >
                                            <Trash2 class="size-4" />
                                        </button>
                                    </div>
                                    <div v-if="!dynamicTokenRequest.body.length" class="rounded-md border border-dashed border-[var(--border-main)] px-3 py-4 text-center text-xs text-[var(--text-tertiary)]">
                                        {{ t('No parameters configured') }}
                                    </div>
                                </div>
                            </div>

                            <div class="grid gap-2">
                                <label class="text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">{{ t('Token Request JSON') }}</label>
                                <textarea
                                    v-model="dynamicTokenRequestJsonInput"
                                    rows="9"
                                    class="min-h-[204px] rounded-lg border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 py-2 font-mono text-xs shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                    :class="dynamicTokenRequestJsonError && 'border-red-300 focus-visible:ring-red-300'"
                                    :placeholder="dynamicTokenRequestJsonPlaceholder"
                                    @input="applyDynamicRequestJsonInput"
                                ></textarea>
                                <p v-if="dynamicTokenRequestJsonError" class="text-[11px] text-red-500">{{ dynamicTokenRequestJsonError }}</p>
                            </div>
                        </div>

                        <div v-if="dynamicTokenTestResult || dynamicTokenTestError" class="grid gap-3 rounded-lg border border-[var(--border-light)] bg-[var(--background-white-main)] p-3">
                            <div class="flex items-center justify-between gap-3">
                                <div>
                                    <div class="text-sm font-semibold text-[var(--text-primary)]">{{ t('Token Test Result') }}</div>
                                    <div class="text-[11px]" :class="dynamicTokenTestResult?.ok ? 'text-emerald-600' : 'text-red-500'">
                                        {{ dynamicTokenTestError || `${t('HTTP Status')}: ${dynamicTokenTestResult?.status_code}` }}
                                    </div>
                                </div>
                            </div>
                            <div v-if="dynamicTokenTestResult" class="grid grid-cols-1 gap-3 lg:grid-cols-[1.2fr_1fr]">
                                <div class="grid gap-2">
                                    <label class="text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">{{ t('Token Response JSON') }}</label>
                                    <pre class="max-h-64 overflow-auto rounded-lg border border-[var(--border-main)] bg-[var(--fill-input-chat)] p-3 text-xs leading-5"><code>{{ JSON.stringify(dynamicTokenTestResult.body, null, 2) }}</code></pre>
                                </div>
                                <div class="grid gap-2">
                                    <label class="text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">{{ t('Response Field Pool') }}</label>
                                    <div class="max-h-64 overflow-auto rounded-lg border border-[var(--border-main)] bg-[var(--fill-input-chat)] p-2">
                                        <button
                                            v-for="field in dynamicTokenTestResult.fields"
                                            :key="field.path"
                                            type="button"
                                            class="mb-1 flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left font-mono text-[11px] transition-colors hover:bg-blue-50 hover:text-blue-700 dark:hover:bg-blue-500/10 dark:hover:text-blue-200"
                                            :title="t('Click field to fill current injection value')"
                                            @click="insertDynamicResponseField(field.path)"
                                        >
                                            <span class="truncate">{{ field.path }}</span>
                                            <span class="max-w-[40%] truncate text-[var(--text-tertiary)]">{{ formatDynamicFieldValue(field.value) }}</span>
                                        </button>
                                        <div v-if="!dynamicTokenTestResult.fields.length" class="px-2 py-3 text-xs text-[var(--text-tertiary)]">{{ t('No response fields') }}</div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="grid gap-3 rounded-lg border border-[var(--border-light)] bg-[var(--background-white-main)] p-3">
                            <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                                <div>
                                    <div class="text-sm font-semibold text-[var(--text-primary)]">{{ t('2. Inject Into Model Request') }}</div>
                                    <div class="text-[11px] text-[var(--text-tertiary)]">{{ t('Inject Into Model Request Hint') }}</div>
                                </div>
                            </div>

                            <div class="overflow-hidden rounded-lg border border-[var(--border-main)] bg-[var(--background-white-main)]">
                                <div class="grid grid-cols-[1fr_1.6fr_44px] items-center border-b border-[var(--border-light)] px-3 py-2 text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
                                    <span>{{ t('Header Name') }}</span>
                                    <span>{{ t('Header Value') }}</span>
                                    <button
                                        type="button"
                                        class="flex size-8 items-center justify-center justify-self-end rounded-md text-blue-600 transition-colors hover:bg-blue-50 dark:hover:bg-blue-500/10"
                                        :title="t('Add Parameter')"
                                        @click="addDynamicInjectRow()"
                                    >
                                        <Plus class="size-4" />
                                    </button>
                                </div>
                                <div class="grid gap-2 p-3">
                                    <div
                                        v-for="(row, index) in dynamicTokenInject.headers"
                                        :key="row.localId"
                                        class="grid grid-cols-[1fr_1.6fr_44px] gap-2"
                                    >
                                        <input
                                            v-model="row.name"
                                            class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                            placeholder="Authorization"
                                        />
                                        <input
                                            v-model="row.value"
                                            class="h-9 rounded-md border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-2 font-mono text-sm shadow-sm placeholder:text-[var(--text-disable)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                                            placeholder="Bearer {$.data.access_token}"
                                            @focus="setActiveDynamicInjectValue(index)"
                                        />
                                        <button
                                            type="button"
                                            class="flex size-9 items-center justify-center rounded-md text-[var(--text-secondary)] transition-all hover:bg-red-50 hover:text-red-600"
                                            :title="t('Delete')"
                                            @click="removeDynamicInjectRow(index)"
                                        >
                                            <Trash2 class="size-4" />
                                        </button>
                                    </div>
                                    <div v-if="!dynamicTokenInject.headers.length" class="rounded-md border border-dashed border-[var(--border-main)] px-3 py-4 text-center text-xs text-[var(--text-tertiary)]">
                                        {{ t('No parameters configured') }}
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="grid gap-2">
                            <button
                                type="button"
                                class="flex h-9 items-center justify-between rounded-md border border-[var(--border-main)] px-3 text-left text-xs font-semibold text-[var(--text-secondary)] transition-colors hover:bg-[var(--fill-tsp-gray-main)]"
                                @click="dynamicAdvancedOpen = !dynamicAdvancedOpen"
                            >
                                <span>{{ t('JSON Preview') }}</span>
                                <ChevronDown class="size-4 transition-transform" :class="dynamicAdvancedOpen && 'rotate-180'" />
                            </button>
                            <textarea
                                v-if="dynamicAdvancedOpen"
                                :value="dynamicTokenJsonPreview"
                                readonly
                                rows="12"
                                class="min-h-[240px] rounded-lg border border-[var(--border-main)] bg-[var(--fill-input-chat)] px-3 py-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--border-dark)]"
                            ></textarea>
                        </div>
                    </div>
                </div>
                </div>
            </div>
        </div>

        <DialogFooter class="px-4 py-4 sm:px-6 bg-gray-50/50 dark:bg-gray-800/30 border-t border-gray-100 dark:border-gray-800 flex flex-col gap-3 sm:flex-row sm:justify-between sm:items-center">
          <div class="text-xs text-gray-400 min-h-[16px]">{{ saving ? t('Verifying connection...') : '' }}</div>
          <div class="flex gap-3 sm:ml-auto w-full sm:w-auto">
            <button
                class="flex-1 sm:flex-none px-5 py-2.5 text-sm font-medium text-gray-600 dark:text-gray-300 bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-600 transition-all shadow-sm"
                @click="isEditOpen = false"
            >
                {{ t('Cancel') }}
            </button>
            <button
                class="flex-1 sm:flex-none px-5 py-2.5 text-sm font-semibold text-white bg-gradient-to-r from-blue-500 to-indigo-600 rounded-xl shadow-md shadow-blue-500/20 hover:shadow-lg hover:shadow-blue-500/30 active:scale-[0.97] transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                :disabled="saving"
                @click="saveModel"
            >
                <Loader2 v-if="saving" class="size-4 animate-spin" />
                <span v-else>{{ t('Save & Verify') }}</span>
            </button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, reactive, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { Plus, Pencil, Trash2, Loader2, Box, CheckCircle2, ShieldCheck, Globe, Key, Radar, ChevronDown, Check } from 'lucide-vue-next';
import ProviderIcon from '../icons/ProviderIcon.vue';
import { 
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter 
} from '@/components/ui/dialog';
import { 
  listModels, createModel, updateModel, deleteModel, detectContextWindow, testDynamicToken,
  type DynamicTokenTestResult,
  type DynamicTokenCredentialSaveInput,
  type DynamicTokenSaveInput,
  type ModelAuthSaveRequest,
  type ModelConfig,
  type StaticHeaderSaveInput,
} from '@/api/models';
import { listCredentials, type Credential } from '@/api/credential';
import { showSuccessToast, showErrorToast } from '@/utils/toast';

const { t } = useI18n();

const PROVIDER_CONFIG: Record<string, { base_url: string; models: string[] }> = {
  openai: {
    base_url: 'https://api.openai.com/v1',
    models: [
      'gpt-5.4', 'gpt-5.3', 'gpt-5.2', 'gpt-5.1', 'gpt-4.5',
    ],
  },
  anthropic: {
    base_url: 'https://api.anthropic.com/v1',
    models: [
      'Claude Opus 4.6', 'Claude Sonnet 4.6',
      'Claude Opus 4.5', 'Claude Sonnet 4',
      'Claude 3.7 Sonnet',
    ],
  },
  deepseek: {
    base_url: 'https://api.deepseek.com',
    models: ['deepseek-chat', 'deepseek-reasoner'],
  },
  gemini: {
    base_url: '',
    models: [
      'gemini-3.1-pro-preview', 'gemini-3.1-flash-preview', 'gemini-3.1-flash-lite-preview', 'gemini-3-deep-think-preview',
    ],
  },
  glm: {
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    models: [
      'GLM-5', 'GLM-4.7', 'GLM-4', 'GLM-3', 'ChatGLM3',
    ],
  },
  qwen: {
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    models: [
      'Qwen3', 'Qwen2.5-72B', 'Qwen2.5-32B', 'Qwen2.5-14B', 'Qwen2.5-7B',
    ],
  },
  kimi: {
    base_url: 'https://api.moonshot.ai/v1',
    models: [
      'kimi-k2.5', 'kimi-k2-0905-preview', 'kimi-k2-turbo-preview', 'kimi-k2-0711-preview', 'kimi-k2-thinking', 'kimi-k2-thinking-turbo',
    ],
  },
  minimax: {
    base_url: 'https://api.minimax.chat/v1',
    models: [
      'MiniMax-M1', 'MiniMax-T1', 'abab6.5s', 'abab6.5', 'abab5.5',
    ],
  },
};

const models = ref<ModelConfig[]>([]);
const credentials = ref<Credential[]>([]);
const loading = ref(false);
const saving = ref(false);
const detecting = ref(false);
const isEditOpen = ref(false);
const editingModel = ref<ModelConfig | null>(null);
const modelDropdownOpen = ref(false);
const customModelInput = ref('');
const dropdownRef = ref<HTMLElement | null>(null);
const authDropdownRef = ref<HTMLElement | null>(null);
const authOptionsOpen = ref(false);
const authMode = ref<'none' | 'static_headers' | 'dynamic_token'>('none');
const selectedAuthCredentialId = ref('');
const staticHeaders = ref<Array<StaticHeaderSaveInput & { localId: string; value: string }>>([]);
const staticAuthJsonInput = ref('');
const staticAuthJsonError = ref('');
const staticAuthJsonPlaceholder = `{
  "X-Gateway-Token": "static-token",
  "X-Tenant": "tenant-a"
}`;
const dynamicCredentials = ref<Array<DynamicTokenCredentialSaveInput & { localId: string; password: string; username: string; domain: string }>>([]);
type DynamicBodyType = 'json' | 'form';
type DynamicInjectRow = { localId: string; name: string; value: string };
type DynamicRequestSection = 'headers' | 'query' | 'body';

const newDynamicParamRow = (overrides: Partial<Pick<DynamicInjectRow, 'name' | 'value'>> = {}): DynamicInjectRow => ({
  localId: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
  name: overrides.name || '',
  value: overrides.value || '',
});
const newDynamicInjectRow = newDynamicParamRow;
const dynamicTokenRequest = reactive({
  method: 'POST' as 'GET' | 'POST' | 'PUT' | 'PATCH',
  url: 'https://auth.company.com/token',
  headers: [newDynamicParamRow({ name: 'Content-Type', value: 'application/json' })],
  query: [] as DynamicInjectRow[],
  bodyType: 'json' as DynamicBodyType,
  body: [
    newDynamicParamRow({ name: 'client_id', value: 'your-client-id' }),
    newDynamicParamRow({ name: 'client_secret', value: 'your-client-secret' }),
  ],
});
const dynamicTokenInject = reactive({
  headers: [newDynamicInjectRow({ name: 'Authorization', value: 'Bearer {$.data.access_token}' })],
});
const activeDynamicInjectValueIndex = ref(0);
const dynamicAdvancedOpen = ref(false);
const dynamicTokenJsonInput = ref('');
const dynamicTokenRequestJsonInput = ref('');
const dynamicTokenRequestJsonError = ref('');
const dynamicTokenRequestJsonPlaceholder = `{
  "method": "POST",
  "url": "https://auth.company.com/token",
  "headers": {
    "Content-Type": "application/json"
  },
  "query": {},
  "body_type": "json",
  "body": {
    "client_id": "your-client-id",
    "client_secret": "your-client-secret"
  }
}`;
const testingDynamicToken = ref(false);
const dynamicTokenTestResult = ref<DynamicTokenTestResult | null>(null);
const dynamicTokenTestError = ref('');

const form = reactive({
  name: '',
  provider: 'openai',
  model_name: '',
  base_url: '',
  api_key: '',
  context_window: null as number | null,
});

const authorizationHeaderValue = computed(() => {
  return form.api_key ? `Bearer ${form.api_key}` : '';
});

const apiKeyFromAuthorizationHeader = (value: string) => {
  const trimmed = value.trim();
  return trimmed.replace(/^Bearer\s+/i, '').trim();
};

const updateAuthorizationHeaderValue = (event: Event) => {
  form.api_key = apiKeyFromAuthorizationHeader((event.target as HTMLInputElement).value);
};

const modelAuthCredentials = computed(() => credentials.value.filter(item => item.kind === 'model_auth'));
const selectedAuthCredential = computed(() => modelAuthCredentials.value.find(item => item.id === selectedAuthCredentialId.value) || null);

const newStaticHeaderRow = (overrides: Partial<StaticHeaderSaveInput & { value: string }> = {}) => ({
  localId: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
  name: overrides.name || '',
  value: overrides.value || '',
  credential_id: overrides.credential_id,
});

const staticHeadersToObject = () => {
  return staticHeaders.value.reduce<Record<string, string>>((acc, row) => {
    const name = row.name.trim();
    if (name) {
      acc[name] = row.value || '';
    }
    return acc;
  }, {});
};

const syncStaticAuthJsonFromRows = () => {
  staticAuthJsonInput.value = JSON.stringify(staticHeadersToObject(), null, 2);
  staticAuthJsonError.value = '';
};

const newDynamicCredentialRow = (overrides: Partial<DynamicTokenCredentialSaveInput> = {}) => ({
  localId: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
  alias: overrides.alias || '',
  username: overrides.username || '',
  password: overrides.password || '',
  domain: overrides.domain || '',
  credential_id: overrides.credential_id,
  name: overrides.name,
});

const parseJsonObject = (raw: string, label: string) => {
  const parsed = JSON.parse(raw || '{}');
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error(t(label) + ': ' + t('JSON must be an object'));
  }
  return parsed as Record<string, unknown>;
};

const objectToDynamicRows = (value: unknown) => {
  if (!value || Array.isArray(value) || typeof value !== 'object') return [];
  return Object.entries(value as Record<string, unknown>).map(([name, raw]) => newDynamicParamRow({
    name,
    value: typeof raw === 'string' ? raw : JSON.stringify(raw),
  }));
};

const dynamicRowsToObject = (rows: DynamicInjectRow[], _label: 'Header' | 'Query' | 'Body', strict = true) => {
  const result: Record<string, string> = {};
  for (const row of rows) {
    const name = row.name.trim();
    const value = row.value;
    if (!name && !String(value || '').trim()) continue;
    if (!name) {
      if (!strict) continue;
      throw new Error(t('Parameter name cannot be empty'));
    }
    const normalized = name.toLowerCase();
    const duplicate = Object.keys(result).some(key => key.toLowerCase() === normalized);
    if (duplicate) {
      if (!strict) continue;
      throw new Error(t('Parameter name duplicated'));
    }
    result[name] = value;
  }
  return result;
};

const dynamicBodyRowsToObject = (rows: DynamicInjectRow[], strict = true) => {
  const result: Record<string, unknown> = {};
  for (const row of rows) {
    const name = row.name.trim();
    const value = row.value;
    if (!name && !String(value || '').trim()) continue;
    if (!name) {
      if (!strict) continue;
      throw new Error(t('Parameter name cannot be empty'));
    }
    const normalized = name.toLowerCase();
    const duplicate = Object.keys(result).some(key => key.toLowerCase() === normalized);
    if (duplicate) {
      if (!strict) continue;
      throw new Error(t('Parameter name duplicated'));
    }
    const trimmed = String(value || '').trim();
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        result[name] = JSON.parse(trimmed);
      } catch {
        result[name] = value;
      }
    } else {
      result[name] = value;
    }
  }
  return result;
};

const buildTokenRequestFromRows = (strict = true) => ({
  method: dynamicTokenRequest.method,
  url: dynamicTokenRequest.url.trim(),
  headers: dynamicRowsToObject(dynamicTokenRequest.headers, 'Header', strict),
  query: dynamicRowsToObject(dynamicTokenRequest.query, 'Query', strict),
  body_type: dynamicTokenRequest.bodyType,
  body: dynamicBodyRowsToObject(dynamicTokenRequest.body, strict),
});

const syncDynamicRequestJsonFromRows = () => {
  dynamicTokenRequestJsonInput.value = JSON.stringify(buildTokenRequestFromRows(false), null, 2);
  dynamicTokenRequestJsonError.value = '';
};

const applyDynamicRequestJsonInput = () => {
  try {
    const parsed = parseJsonObject(dynamicTokenRequestJsonInput.value, 'Token Request JSON');
    const tokenRequest = (parsed.token_request && typeof parsed.token_request === 'object' && !Array.isArray(parsed.token_request))
      ? parsed.token_request as Record<string, unknown>
      : parsed;
    const method = String(tokenRequest.method || dynamicTokenRequest.method).toUpperCase();
    dynamicTokenRequest.method = (['GET', 'POST', 'PUT', 'PATCH'].includes(method) ? method : 'POST') as typeof dynamicTokenRequest.method;
    dynamicTokenRequest.url = String(tokenRequest.url || '');
    const headers = tokenRequest.headers ?? tokenRequest.header ?? {};
    const query = tokenRequest.query ?? tokenRequest.params ?? {};
    const body = tokenRequest.body ?? {};
    dynamicTokenRequest.headers = objectToDynamicRows(headers);
    dynamicTokenRequest.query = objectToDynamicRows(query);
    dynamicTokenRequest.body = objectToDynamicRows(body);
    dynamicTokenRequest.bodyType = (tokenRequest.body_type === 'form' ? 'form' : 'json') as DynamicBodyType;
    dynamicTokenRequestJsonError.value = '';
  } catch (error: any) {
    dynamicTokenRequestJsonError.value = error?.message || t('Dynamic Token JSON invalid');
  }
};

const resetDynamicForm = () => {
  dynamicTokenRequest.method = 'POST';
  dynamicTokenRequest.url = 'https://auth.company.com/token';
  dynamicTokenRequest.headers = [newDynamicParamRow({ name: 'Content-Type', value: 'application/json' })];
  dynamicTokenRequest.query = [];
  dynamicTokenRequest.bodyType = 'json';
  dynamicTokenRequest.body = [
    newDynamicParamRow({ name: 'client_id', value: 'your-client-id' }),
    newDynamicParamRow({ name: 'client_secret', value: 'your-client-secret' }),
  ];
  dynamicTokenInject.headers = [newDynamicInjectRow({ name: 'Authorization', value: 'Bearer {$.data.access_token}' })];
  activeDynamicInjectValueIndex.value = 0;
  dynamicAdvancedOpen.value = false;
  dynamicTokenTestResult.value = null;
  dynamicTokenTestError.value = '';
  syncDynamicRequestJsonFromRows();
};

const systemModels = computed(() => models.value.filter(m => m.is_system));
const userModels = computed(() => models.value.filter(m => !m.is_system));
const authSummaryText = computed(() => {
  if (selectedAuthCredential.value) return selectedAuthCredential.value.name;
  if (authMode.value === 'static_headers') return t('Static Token');
  if (authMode.value === 'dynamic_token') return t('Dynamic Token');
  return t('No Extra Auth');
});

const buildDynamicConfigFromForm = (): DynamicTokenSaveInput => {
  if (dynamicTokenRequestJsonError.value) {
    throw new Error(dynamicTokenRequestJsonError.value);
  }
  const tokenRequest = buildTokenRequestFromRows(true);
  const inject = {
    headers: dynamicInjectRowsToObject(dynamicTokenInject.headers, 'Header'),
    query: {},
    body: {} as Record<string, unknown>,
  };

  return {
    credentials: [],
    token_request: {
      method: tokenRequest.method,
      url: tokenRequest.url,
      headers: tokenRequest.headers,
      query: tokenRequest.query,
      body_type: tokenRequest.body_type,
      body: tokenRequest.body,
    },
    inject,
  };
};

const formatDynamicFieldValue = (value: unknown) => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
};

const dynamicInjectRowsToObject = (rows: DynamicInjectRow[], label: 'Header') => {
  const result: Record<string, string> = {};
  for (const row of rows) {
    const name = row.name.trim();
    const value = row.value;
    if (!name && !String(value || '').trim()) continue;
    if (!name) throw new Error(t('Header name cannot be empty'));
    const normalized = name.toLowerCase();
    const duplicate = Object.keys(result).some(key => key.toLowerCase() === normalized);
    if (duplicate) throw new Error(t('Header name duplicated'));
    result[name] = value;
  }
  return result;
};

const dynamicRequestRowsForSection = (section: DynamicRequestSection) => dynamicTokenRequest[section];

const addDynamicRequestRow = (section: DynamicRequestSection) => {
  const rows = dynamicRequestRowsForSection(section);
  rows.push(newDynamicParamRow());
  syncDynamicRequestJsonFromRows();
};

const updateDynamicRequestRow = (
  section: DynamicRequestSection,
  index: number,
  field: 'name' | 'value',
  event: Event,
) => {
  const rows = dynamicRequestRowsForSection(section);
  const row = rows[index];
  if (!row) return;
  row[field] = (event.target as HTMLInputElement).value;
  syncDynamicRequestJsonFromRows();
};

const removeDynamicRequestRow = (section: DynamicRequestSection, index: number) => {
  dynamicRequestRowsForSection(section).splice(index, 1);
  syncDynamicRequestJsonFromRows();
};

const setActiveDynamicInjectValue = (index: number) => {
  activeDynamicInjectValueIndex.value = index;
};

const addDynamicInjectRow = (row?: Partial<Pick<DynamicInjectRow, 'name' | 'value'>>) => {
  dynamicTokenInject.headers.push(newDynamicInjectRow(row));
  activeDynamicInjectValueIndex.value = dynamicTokenInject.headers.length - 1;
};

const removeDynamicInjectRow = (index: number) => {
  dynamicTokenInject.headers.splice(index, 1);
  activeDynamicInjectValueIndex.value = Math.max(0, Math.min(index, dynamicTokenInject.headers.length - 1));
};

const insertDynamicResponseField = (path: string) => {
  const template = `{${path}}`;
  if (!dynamicTokenInject.headers.length) addDynamicInjectRow();
  const index = Math.min(activeDynamicInjectValueIndex.value, dynamicTokenInject.headers.length - 1);
  const row = dynamicTokenInject.headers[index];
  const headerName = row.name.trim().toLowerCase();
  row.value = headerName === 'authorization' ? `Bearer ${template}` : template;
};

const runDynamicTokenTest = async () => {
  testingDynamicToken.value = true;
  dynamicTokenTestError.value = '';
  try {
    const config = buildDynamicConfigFromForm();
    if (!String(config.token_request.url || '').trim()) {
      throw new Error(t('Token URL is required'));
    }
    const result = await testDynamicToken(config);
    dynamicTokenTestResult.value = result;
    if (result.ok) {
      showSuccessToast(t('Token test succeeded'));
    } else {
      dynamicTokenTestError.value = `${t('Token test failed')}: HTTP ${result.status_code}`;
    }
  } catch (error: any) {
    dynamicTokenTestResult.value = null;
    dynamicTokenTestError.value = error?.response?.data?.detail || error?.message || t('Token test failed');
    showErrorToast(dynamicTokenTestError.value);
  } finally {
    testingDynamicToken.value = false;
  }
};

const dynamicTokenJsonPreview = computed(() => {
  try {
    const config = buildDynamicConfigFromForm();
    return JSON.stringify({
      token_request: config.token_request,
      inject: config.inject,
    }, null, 2);
  } catch {
    return dynamicTokenJsonInput.value || '';
  }
});

const providerModels = computed(() => {
  const config = PROVIDER_CONFIG[form.provider];
  return config?.models || [];
});

const selectProvider = (p: string) => {
  form.provider = p;
  modelDropdownOpen.value = false;
  customModelInput.value = '';
  if (p !== 'other') {
    const config = PROVIDER_CONFIG[p];
    if (config) {
      form.base_url = config.base_url;
      const first = config.models[0] || '';
      form.model_name = first;
      form.name = first;
    }
  }
};

const onModelSelect = () => {
  if (form.model_name && (!form.name || providerModels.value.includes(form.name))) {
    form.name = form.model_name;
  }
};

const selectModel = (m: string) => {
  form.model_name = m;
  form.name = m;
  modelDropdownOpen.value = false;
  customModelInput.value = '';
};

const applyCustomModel = () => {
  const val = customModelInput.value.trim();
  if (!val) return;
  form.model_name = val;
  form.name = val;
  modelDropdownOpen.value = false;
  customModelInput.value = '';
};

const onClickOutside = (e: MouseEvent) => {
  if (dropdownRef.value && !dropdownRef.value.contains(e.target as Node)) {
    modelDropdownOpen.value = false;
  }
  if (authDropdownRef.value && !authDropdownRef.value.contains(e.target as Node)) {
    authOptionsOpen.value = false;
  }
};

const configuredStaticHeadersFromModel = (model: ModelConfig | null) => {
  if (!model?.auth_config || model.auth_config.type !== 'static_headers') return [];
  const aliasToCredential = new Map(
    (model.auth_config.credentials || []).map(item => [item.alias, item.credential_id]),
  );
  return Object.entries(model.auth_config.headers || {}).map(([name, template]) => {
    const alias = String(template || '').match(/{{\s*([A-Za-z_][\w-]*)\.password\s*}}/)?.[1] || '';
    return newStaticHeaderRow({
      name,
      value: '',
      credential_id: alias ? aliasToCredential.get(alias) : undefined,
    });
  });
};

const configuredDynamicCredentialsFromModel = (model: ModelConfig | null) => {
  if (!model?.auth_config || model.auth_config.type !== 'dynamic_token') return [];
  return (model.auth_config.credentials || []).map(item => newDynamicCredentialRow({
    alias: item.alias,
    credential_id: item.credential_id,
  }));
};

const applyDynamicConfigToForm = (config: DynamicTokenSaveInput) => {
  resetDynamicForm();
  const tokenRequest = config.token_request;
  dynamicTokenRequest.method = tokenRequest.method || 'POST';
  dynamicTokenRequest.url = tokenRequest.url || '';
  dynamicTokenRequest.headers = objectToDynamicRows(tokenRequest.headers);
  dynamicTokenRequest.query = objectToDynamicRows(tokenRequest.query);
  dynamicTokenRequest.bodyType = tokenRequest.body_type === 'form' ? 'form' : 'json';
  dynamicTokenRequest.body = objectToDynamicRows(tokenRequest.body);
  syncDynamicRequestJsonFromRows();

  const inject = config.inject || { headers: {}, query: {}, body: {} };
  const headerEntries = Object.entries(inject.headers || {});
  dynamicTokenInject.headers = headerEntries.map(([name, value]) => newDynamicInjectRow({ name, value: String(value) }));
  if (dynamicTokenInject.headers.length) {
    activeDynamicInjectValueIndex.value = 0;
  } else {
    dynamicTokenInject.headers = [newDynamicInjectRow({ name: 'Authorization', value: 'Bearer {$.data.access_token}' })];
    activeDynamicInjectValueIndex.value = 0;
  }
};

const dynamicJsonFromModel = (model: ModelConfig | null) => {
  if (!model?.auth_config || model.auth_config.type !== 'dynamic_token') {
    return dynamicTokenJsonPreview.value;
  }
  return JSON.stringify({
    token_request: model.auth_config.token_request,
    inject: model.auth_config.inject || { headers: {}, query: {}, body: {} },
  }, null, 2);
};

const resetAuthForm = (model: ModelConfig | null) => {
  authOptionsOpen.value = false;
  selectedAuthCredentialId.value = model?.auth_credential_id || '';
  if (model?.auth_config?.type === 'static_headers') {
    authMode.value = 'static_headers';
    staticHeaders.value = configuredStaticHeadersFromModel(model);
    dynamicCredentials.value = [];
    resetDynamicForm();
    dynamicTokenJsonInput.value = dynamicTokenJsonPreview.value;
  } else if (model?.auth_config?.type === 'dynamic_token') {
    authMode.value = 'dynamic_token';
    staticHeaders.value = [];
    dynamicCredentials.value = configuredDynamicCredentialsFromModel(model);
    applyDynamicConfigToForm({
      credentials: [],
      token_request: model.auth_config.token_request,
      inject: model.auth_config.inject || { headers: {}, query: {}, body: {} },
    });
    dynamicTokenJsonInput.value = dynamicJsonFromModel(model);
  } else {
    authMode.value = 'none';
    staticHeaders.value = [];
    dynamicCredentials.value = [];
    resetDynamicForm();
    dynamicTokenJsonInput.value = dynamicTokenJsonPreview.value;
  }
  syncStaticAuthJsonFromRows();
};

const setAuthMode = (mode: 'none' | 'static_headers' | 'dynamic_token') => {
  authMode.value = mode;
};

const selectAuthMode = (mode: 'none' | 'static_headers' | 'dynamic_token') => {
  setAuthMode(mode);
  authOptionsOpen.value = false;
};

const addStaticHeader = () => {
  staticHeaders.value.push(newStaticHeaderRow());
  syncStaticAuthJsonFromRows();
};

const removeStaticHeader = (index: number) => {
  staticHeaders.value.splice(index, 1);
  syncStaticAuthJsonFromRows();
};

const updateStaticHeaderField = (
  header: StaticHeaderSaveInput & { localId: string; value: string },
  field: 'name' | 'value',
  event: Event,
) => {
  header[field] = (event.target as HTMLInputElement).value;
  syncStaticAuthJsonFromRows();
  if (field === 'name' && header.name.trim().toLowerCase() === 'authorization') {
    staticAuthJsonError.value = t('Authorization is fixed by API Key');
  }
};

const applyStaticAuthJsonInput = () => {
  const raw = staticAuthJsonInput.value.trim();
  if (!raw) {
    staticHeaders.value = [];
    staticAuthJsonError.value = '';
    return;
  }
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      staticAuthJsonError.value = t('Header JSON must be an object');
      return;
    }
    const headerRows = Object.entries(parsed as Record<string, unknown>).flatMap(([name, value]) => {
      const headerName = String(name).trim();
      if (!headerName) throw new Error('empty-name');
      if (headerName.toLowerCase() === 'authorization') {
        form.api_key = apiKeyFromAuthorizationHeader(String(value));
        return [];
      }
      return [newStaticHeaderRow({ name: headerName, value: String(value) })];
    });
    staticHeaders.value = headerRows;
    syncStaticAuthJsonFromRows();
  } catch (err: any) {
    if (err?.message === 'empty-name') {
      staticAuthJsonError.value = t('Header name cannot be empty');
    } else {
      staticAuthJsonError.value = t('Header JSON must be an object');
    }
  }
};

const buildDynamicTokenConfig = (): DynamicTokenSaveInput => {
  const config = buildDynamicConfigFromForm();
  const tokenRequest = config.token_request;
  if (!tokenRequest || Array.isArray(tokenRequest) || typeof tokenRequest !== 'object') {
    throw new Error(t('Token request is required'));
  }
  if (!String(tokenRequest.url || '').trim()) {
    throw new Error(t('Token URL is required'));
  }
  dynamicTokenJsonInput.value = dynamicTokenJsonPreview.value;
  return config;
};

const buildAuthPayload = (): ModelAuthSaveRequest => {
  if (authMode.value === 'none') {
    return { type: 'none' };
  }
  if (authMode.value === 'dynamic_token') {
    return { type: 'dynamic_token', dynamic_token: buildDynamicTokenConfig() };
  }
  if (staticAuthJsonError.value) {
    throw new Error(staticAuthJsonError.value);
  }
  const seen = new Set<string>();
  const rows = staticHeaders.value
    .map(row => ({
      name: row.name.trim(),
      value: row.value || undefined,
      credential_id: row.credential_id || undefined,
    }))
    .filter(row => row.name || row.value || row.credential_id);
  for (const row of rows) {
    if (!row.name) throw new Error(t('Header name cannot be empty'));
    const key = row.name.toLowerCase();
    if (key === 'authorization') throw new Error(t('Authorization is fixed by API Key'));
    if (seen.has(key)) throw new Error(t('Header name duplicated') + `: ${row.name}`);
    seen.add(key);
    if (!row.value && !row.credential_id) throw new Error(t('Header value is required') + `: ${row.name}`);
  }
  if (!rows.length) {
    return { type: 'none' };
  }
  return { type: 'static_headers', static_headers: rows };
};

const fetchModels = async () => {
  loading.value = true;
  try {
    models.value = await listModels();
  } catch (err) {
    console.error(err);
    showErrorToast(t('Failed to load models'));
  } finally {
    loading.value = false;
  }
};

const fetchCredentials = async () => {
  try {
    credentials.value = await listCredentials();
  } catch (err) {
    console.error(err);
  }
};

const openEditModal = (model: ModelConfig | null) => {
  editingModel.value = model;
  if (model) {
    form.name = model.name;
    form.provider = model.provider;
    form.model_name = model.model_name;
    form.base_url = model.base_url || '';
    form.api_key = '';
    form.context_window = model.context_window ?? null;
    resetAuthForm(model);
  } else {
    const defaultProvider = 'openai';
    const firstModel = PROVIDER_CONFIG[defaultProvider]?.models[0] || '';
    form.name = firstModel;
    form.provider = defaultProvider;
    form.model_name = firstModel;
    form.base_url = PROVIDER_CONFIG[defaultProvider]?.base_url || '';
    form.api_key = '';
    form.context_window = null;
    resetAuthForm(null);
  }
  isEditOpen.value = true;
};

const saveModel = async () => {
  if (!form.name || !form.model_name || !form.provider) {
    showErrorToast(t('Please fill in all required fields'));
    return;
  }

  let auth_config: ModelAuthSaveRequest;
  try {
    auth_config = buildAuthPayload();
  } catch (err: any) {
    showErrorToast(t('Operation failed') + ': ' + (err.message || String(err)));
    return;
  }

  if (!editingModel.value && !form.api_key && auth_config.type === 'none' && !selectedAuthCredentialId.value) {
      showErrorToast(t('API Key is required'));
      return;
  }

  saving.value = true;
  try {
    const ctxWindow = form.context_window && form.context_window >= 1024 ? form.context_window : undefined;
    if (editingModel.value) {
      const payload = {
        name: form.name,
        base_url: form.base_url || undefined,
        api_key: form.api_key || undefined,
        model_name: form.model_name,
        context_window: ctxWindow ?? null,
      } as Parameters<typeof updateModel>[1];
      if (selectedAuthCredentialId.value) {
        payload.auth_credential_id = selectedAuthCredentialId.value;
      } else {
        payload.auth_credential_id = null;
        payload.auth_config = auth_config;
      }
      await updateModel(editingModel.value.id, payload);
      showSuccessToast(t('Model verified & updated'));
    } else {
      await createModel({
        name: form.name,
        provider: form.provider,
        base_url: form.base_url || undefined,
        api_key: form.api_key || undefined,
        model_name: form.model_name,
        context_window: ctxWindow ?? null,
        auth_config: selectedAuthCredentialId.value ? null : auth_config,
        auth_credential_id: selectedAuthCredentialId.value || null,
      });
      showSuccessToast(t('Model verified & created'));
    }
    isEditOpen.value = false;
    await fetchModels();
    await fetchCredentials();
  } catch (err: any) {
    console.error(err);
    const detail = err.response?.data?.detail || err.response?.data?.message || err.message || String(err);
    showErrorToast(t('Operation failed') + ': ' + detail);
  } finally {
    saving.value = false;
  }
};

const detectCtxWindow = async () => {
  if (!form.model_name) {
    showErrorToast(t('Please enter Model ID first'));
    return;
  }
  detecting.value = true;
  try {
    const ctxWindow = await detectContextWindow({
      provider: form.provider,
      base_url: form.base_url || undefined,
      api_key: form.api_key || undefined,
      model_name: form.model_name,
      model_id: editingModel.value?.id,
    });
    form.context_window = ctxWindow;
    showSuccessToast(t('Detected context window') + `: ${ctxWindow.toLocaleString()}`);
  } catch (err: any) {
    const detail = err.response?.data?.detail;
    showErrorToast(detail || t('Failed to detect context window'));
  } finally {
    detecting.value = false;
  }
};

const confirmDelete = async (model: ModelConfig) => {
  if (!confirm(t('Are you sure you want to delete this model?'))) return;
  
  try {
    await deleteModel(model.id);
    showSuccessToast(t('Model deleted'));
    await fetchModels();
  } catch (err) {
    console.error(err);
    showErrorToast(t('Delete failed'));
  }
};

onMounted(() => {
  fetchModels();
  fetchCredentials();
  document.addEventListener('click', onClickOutside);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', onClickOutside);
});
</script>
