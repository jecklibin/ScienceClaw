<template>
    <Teleport to="body">
        <Transition name="dialog">
            <div v-if="dialogVisible" class="fixed inset-0 z-[9999] flex items-center justify-center px-4 py-6">
                <div class="absolute inset-0 bg-slate-950/55 backdrop-blur-sm" @click="handleBackdropClick"></div>
                <div
                    class="relative z-10 w-full max-w-md overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-2xl dark:border-white/10 dark:bg-[#17181d]"
                    role="alertdialog"
                    aria-modal="true"
                    aria-labelledby="custom-dialog-title"
                    :aria-describedby="dialogConfig.content ? 'custom-dialog-description' : undefined"
                >
                    <div class="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5 dark:border-white/10">
                        <div class="flex min-w-0 items-start gap-3">
                            <div
                                data-testid="dialog-tone-icon"
                                class="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl"
                                :class="dialogConfig.confirmType === 'danger'
                                    ? 'bg-red-50 text-red-600 ring-1 ring-red-100 dark:bg-red-500/10 dark:text-red-300 dark:ring-red-400/20'
                                    : 'bg-blue-50 text-blue-700 ring-1 ring-blue-100 dark:bg-blue-400/10 dark:text-blue-200 dark:ring-blue-400/20'"
                            >
                                <AlertTriangle v-if="dialogConfig.confirmType === 'danger'" :size="20" />
                                <CheckCircle2 v-else :size="20" />
                            </div>
                            <div class="min-w-0">
                                <h3 id="custom-dialog-title" class="text-lg font-black leading-6 text-[var(--text-primary)]">{{ dialogConfig.title }}</h3>
                                <p
                                    v-if="dialogConfig.content"
                                    id="custom-dialog-description"
                                    class="mt-2 text-sm leading-6 text-[var(--text-secondary)]"
                                >
                                    {{ dialogConfig.content }}
                                </p>
                            </div>
                        </div>
                        <button
                            class="rounded-xl p-2 text-[var(--text-tertiary)] transition hover:bg-slate-100 hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-white/10"
                            :disabled="isConfirming"
                            @click="handleDialogCancel"
                        >
                            <X :size="18" />
                        </button>
                    </div>
                    <div class="flex justify-end gap-3 bg-slate-50 px-6 py-5 dark:bg-white/[0.04]">
                        <button
                            class="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-[var(--text-secondary)] transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10"
                            :disabled="isConfirming"
                            @click="handleDialogCancel"
                        >
                            {{ dialogConfig.cancelText }}
                        </button>
                        <button
                            :class="[
                                'inline-flex min-w-[88px] items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-black transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-70',
                                dialogConfig.confirmType === 'danger'
                                    ? 'bg-red-600 text-white shadow-lg shadow-red-500/20 hover:bg-red-700 active:scale-[0.98]'
                                    : 'bg-gradient-to-br from-[#8930b0] to-[#004be2] text-white shadow-lg shadow-blue-500/20 hover:-translate-y-0.5 active:translate-y-0'
                            ]"
                            :disabled="isConfirming"
                            @click="handleDialogConfirm"
                        >
                            <Loader2 v-if="isConfirming" class="animate-spin" :size="15" />
                            {{ dialogConfig.confirmText }}
                        </button>
                    </div>
                </div>
            </div>
        </Transition>
    </Teleport>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { AlertTriangle, CheckCircle2, Loader2, X } from 'lucide-vue-next'
import { useDialog } from '@/composables/useDialog'

const { dialogVisible, dialogConfig, handleConfirm, handleCancel } = useDialog()
const isConfirming = ref(false)

const handleDialogConfirm = async () => {
    if (isConfirming.value) return
    isConfirming.value = true
    try {
        await handleConfirm()
    } catch (error) {
        console.error(error)
    } finally {
        isConfirming.value = false
    }
}

const handleDialogCancel = () => {
    if (isConfirming.value) return
    handleCancel()
}

const handleBackdropClick = () => { handleDialogCancel() }
</script>

<style scoped>
.dialog-enter-active { transition: all 0.25s ease-out; }
.dialog-leave-active { transition: all 0.2s ease-in; }
.dialog-enter-from, .dialog-leave-to { opacity: 0; }
.dialog-enter-from > div:last-child { transform: scale(0.96) translateY(12px); }
.dialog-leave-to > div:last-child { transform: scale(0.96) translateY(12px); }
</style>
