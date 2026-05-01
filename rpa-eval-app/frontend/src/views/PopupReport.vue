<template>
  <div class="page">
    <h1 class="page-title">Popup Report</h1>
    <div class="panel">
      <p>Report POPUP-2026-RPA-001 is ready for download.</p>
      <button data-testid="download-popup-report" @click="downloadPopupReport">Download popup report</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { apiClient, downloadFromResponse, filenameFromDisposition } from '@/api/client'

async function downloadPopupReport() {
  const response = await apiClient.get('/lab/popup-report/download', { responseType: 'blob' })
  downloadFromResponse(response.data, filenameFromDisposition(response.headers['content-disposition'], 'popup_report_2026.csv'))
}
</script>
