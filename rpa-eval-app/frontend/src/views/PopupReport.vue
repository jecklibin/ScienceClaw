<template>
  <div class="page">
    <h1 class="page-title">Popup Report</h1>
    <div class="panel">
      <p>Report POPUP-2026-RPA-001 is ready for download.</p>
      <div v-if="statusMessage" role="status" aria-live="polite" data-rpa-feedback>{{ statusMessage }}</div>
      <button data-testid="download-popup-report" @click="downloadPopupReport">Download popup_report_2026.csv</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { apiClient, downloadFromResponse, filenameFromDisposition } from '@/api/client'

const statusMessage = ref('')

async function downloadPopupReport() {
  const response = await apiClient.get('/lab/popup-report/download', { responseType: 'blob' })
  const filename = filenameFromDisposition(response.headers['content-disposition'], 'popup_report_2026.csv')
  downloadFromResponse(response.data, filename)
  statusMessage.value = `Report downloaded: ${filename}`
}
</script>
