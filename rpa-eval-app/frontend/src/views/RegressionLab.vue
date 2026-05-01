<template>
  <div class="page">
    <h1 class="page-title">RPA Regression Lab</h1>

    <section class="panel">
      <h2>Weak body click guard</h2>
      <p>Select all export rows, open the toolbar export menu, then choose Export all columns.</p>
      <table class="data-table" data-testid="weak-click-table">
        <thead>
          <tr>
            <th><input data-testid="weak-click-select-all" type="checkbox" v-model="allSelected" /></th>
            <th>Task ID</th>
            <th>File</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in exportRows" :key="row.id">
            <td><input type="checkbox" :checked="allSelected" /></td>
            <td>{{ row.id }}</td>
            <td>{{ row.file }}</td>
            <td>{{ row.status }}</td>
          </tr>
        </tbody>
      </table>
      <div class="toolbar">
        <button data-testid="weak-click-export-trigger" @click="exportMenuOpen = !exportMenuOpen">Export menu</button>
        <div v-if="exportMenuOpen" class="menu" data-testid="weak-click-export-menu">
          <button data-testid="weak-click-export-all" @click="recordSimpleEvent('body_click_export_all', 'EXPORT-ALL-2026')">
            Export all columns
          </button>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2>Split header/body grid</h2>
      <p>Open the file link in the first row of the export result grid. Ignore the navigation table.</p>
      <div class="split-grid-layout">
        <table class="data-table side-table" data-testid="split-grid-distractor">
          <tbody>
            <tr><td>Navigation row</td><td><a href="#" @click.prevent>not-the-target.csv</a></td></tr>
          </tbody>
        </table>
        <div class="split-grid" data-testid="split-grid-container">
          <table class="data-table split-head">
            <thead>
              <tr><th>File ID</th><th>File name</th><th>Owner</th><th>Status</th></tr>
            </thead>
          </table>
          <table class="data-table split-body" data-testid="split-grid-body">
            <tbody>
              <tr v-for="file in files" :key="file.file_id" class="grid-row">
                <td>{{ file.file_id }}</td>
                <td>
                  <a :data-testid="`split-file-${file.file_id}`" href="#" @click.prevent="openSplitFile(file.file_id)">
                    {{ file.file_name }}
                  </a>
                </td>
                <td>{{ file.owner }}</td>
                <td>{{ file.status }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2>Collection locator rewrite guard</h2>
      <p>Open the action link inside the second row of this scoped collection table.</p>
      <table class="data-table" data-testid="collection-rewrite-table">
        <thead>
          <tr><th>Row key</th><th>Name</th><th>Action</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in collectionRows" :key="row.key" class="collection-row">
            <td>{{ row.key }}</td>
            <td>{{ row.name }}</td>
            <td>
              <button :data-testid="`random-action-${row.random}`" @click="recordSimpleEvent('collection_row_action', row.key)">
                Open row action
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Legitimate empty extraction</h2>
      <p>Filter audit records by failed status and report that the result list is empty.</p>
      <div class="toolbar">
        <button data-testid="empty-audit-filter" @click="filterFailedAudits">Show failed records</button>
        <span data-testid="empty-audit-count">Rows: {{ auditRows.length }}</span>
      </div>
      <div v-if="auditFiltered && auditRows.length === 0" data-testid="empty-audit-state" class="empty-state">
        No failed audit records
      </div>
      <table v-else class="data-table">
        <tbody>
          <tr v-for="row in auditRows" :key="row.record_id"><td>{{ row.record_id }}</td><td>{{ row.status }}</td><td>{{ row.summary }}</td></tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Cross-step dataflow form</h2>
      <div class="detail-card" data-testid="dataflow-source-detail">
        <dl>
          <dt>Supplier number</dt><dd data-testid="dataflow-supplier-number">SUP-2026-001</dd>
          <dt>Owner department</dt><dd data-testid="dataflow-department">Procurement Automation</dd>
        </dl>
      </div>
      <div class="form-grid" data-testid="dataflow-form">
        <label>Request number <input data-testid="dataflow-request-number" v-model="dataflowForm.request_number" /></label>
        <label>Supplier number <input data-testid="dataflow-supplier-input" v-model="dataflowForm.supplier_number" /></label>
        <label>Department <input data-testid="dataflow-department-input" v-model="dataflowForm.department" /></label>
        <label>Cost center <input data-testid="dataflow-cost-center" v-model="dataflowForm.cost_center" /></label>
        <button data-testid="dataflow-submit" @click="submitDataflow">Submit dataflow form</button>
      </div>
    </section>

    <section class="panel">
      <h2>Parameterized contract target</h2>
      <p>Search and open the alternate contract, not the recorded default contract.</p>
      <div class="toolbar">
        <input data-testid="parameter-contract-query" v-model="contractQuery" placeholder="Contract number" />
        <button data-testid="parameter-contract-search" @click="searchContracts">Search contracts</button>
      </div>
      <table class="data-table" data-testid="parameter-contract-results">
        <tbody>
          <tr v-for="contract in filteredContracts" :key="contract.number">
            <td>{{ contract.number }}</td>
            <td>{{ contract.title }}</td>
            <td><button :data-testid="`open-${contract.number}`" @click="openParameterizedContract(contract.number)">Open contract</button></td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Modal scoped form</h2>
      <button data-testid="open-modal-supplier" @click="modalOpen = true">Edit supplier in modal</button>
      <div class="background-form">
        <label>Contact person <input data-testid="background-contact-person" value="Do not edit background" /></label>
      </div>
      <div v-if="modalOpen" class="modal-mask" role="dialog" aria-modal="true" aria-label="Supplier contact modal">
        <div class="modal-card" data-testid="supplier-modal">
          <h3>Supplier contact modal</h3>
          <label>Supplier number <input data-testid="modal-supplier-number" v-model="modalForm.supplier_number" /></label>
          <label>Contact person <input data-testid="modal-contact-person" v-model="modalForm.contact_person" /></label>
          <label>Contact phone <input data-testid="modal-contact-phone" v-model="modalForm.contact_phone" /></label>
          <label>Contact email <input data-testid="modal-contact-email" v-model="modalForm.contact_email" /></label>
          <button data-testid="modal-save-supplier" @click="saveModalSupplier">Save modal supplier</button>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2>Popup tab download</h2>
      <button data-testid="open-popup-report" @click="openPopupReport">Open popup report</button>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { apiClient } from '@/api/client'

interface LabFile {
  file_id: string
  file_name: string
  owner: string
  status: string
}

const allSelected = ref(false)
const exportMenuOpen = ref(false)
const files = ref<LabFile[]>([])
const auditRows = ref<Array<Record<string, string>>>([])
const auditFiltered = ref(false)
const contractQuery = ref('')
const filteredContracts = ref([
  { number: 'CT-2026-RPA-001', title: 'Recorded default contract' },
  { number: 'CT-2026-RPA-ALT-001', title: 'RPA regression alternate contract' }
])
const modalOpen = ref(false)

const exportRows = [
  { id: 'EXPORT-ROW-001', file: 'all-columns-source.csv', status: 'ready' },
  { id: 'EXPORT-ROW-002', file: 'summary-source.csv', status: 'ready' }
]

const collectionRows = [
  { key: 'COLLECTION-ROW-001', name: 'First scoped row', random: 'a8f31kq' },
  { key: 'COLLECTION-ROW-002', name: 'Second scoped row', random: 'z9m42xp' }
]

const dataflowForm = reactive({
  request_number: 'PR-2026-RPA-DATAFLOW',
  supplier_number: '',
  department: '',
  cost_center: ''
})

const modalForm = reactive({
  supplier_number: 'SUP-2026-MODAL',
  contact_person: '',
  contact_phone: '',
  contact_email: ''
})

async function recordSimpleEvent(eventKey: string, entityId: string) {
  await apiClient.post(`/lab/events/${eventKey}`, { entity_id: entityId, status: 'completed' })
}

async function openSplitFile(fileId: string) {
  await apiClient.post(`/lab/split-grid/open/${fileId}`)
}

async function filterFailedAudits() {
  const { data } = await apiClient.get('/lab/empty-audit-records', { params: { status: 'failed' } })
  auditRows.value = data
  auditFiltered.value = true
  await recordSimpleEvent('empty_audit_filtered', 'AUDIT-FAILED-EMPTY')
}

function searchContracts() {
  const query = contractQuery.value.trim()
  filteredContracts.value = [
    { number: 'CT-2026-RPA-001', title: 'Recorded default contract' },
    { number: 'CT-2026-RPA-ALT-001', title: 'RPA regression alternate contract' }
  ].filter((item) => !query || item.number.includes(query))
}

async function submitDataflow() {
  await apiClient.post('/lab/dataflow-submit', dataflowForm)
}

async function openParameterizedContract(contractNumber: string) {
  await apiClient.post('/lab/parameterized-contract/open', { contract_number: contractNumber })
}

async function saveModalSupplier() {
  await apiClient.post('/lab/modal-supplier/save', modalForm)
  modalOpen.value = false
}

function openPopupReport() {
  window.open('/regression-lab/popup-report', '_blank', 'noopener,noreferrer')
}

onMounted(async () => {
  const { data } = await apiClient.get<LabFile[]>('/lab/split-grid/files')
  files.value = data
  const audit = await apiClient.get('/lab/empty-audit-records')
  auditRows.value = audit.data
})
</script>

<style scoped>
.panel {
  margin-bottom: 18px;
  padding: 16px;
  border: 1px solid #d8dde8;
  border-radius: 8px;
  background: #fff;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 10px;
}

.data-table th,
.data-table td {
  border: 1px solid #d8dde8;
  padding: 8px;
  text-align: left;
}

.toolbar,
.form-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
  align-items: center;
}

.form-grid label,
.modal-card label {
  display: grid;
  gap: 4px;
}

.form-grid input,
.modal-card input,
.toolbar input {
  min-width: 220px;
  padding: 6px 8px;
  border: 1px solid #b9c1d1;
  border-radius: 4px;
}

.menu {
  display: inline-flex;
  margin-left: 8px;
  padding: 8px;
  border: 1px solid #c9d1df;
  background: #f8fafc;
}

.split-grid-layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 14px;
}

.split-head {
  margin-bottom: 0;
}

.split-body {
  margin-top: 0;
}

.empty-state {
  padding: 16px;
  margin-top: 10px;
  border: 1px dashed #9aa6b8;
  color: #465266;
}

.detail-card {
  padding: 10px;
  background: #f8fafc;
  border: 1px solid #d8dde8;
}

.detail-card dl {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 8px;
  margin: 0;
}

.modal-mask {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  background: rgba(15, 23, 42, 0.35);
  z-index: 50;
}

.modal-card {
  display: grid;
  gap: 10px;
  width: 420px;
  padding: 18px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 16px 48px rgba(15, 23, 42, 0.22);
}

button {
  padding: 7px 12px;
  border: 1px solid #9aa6b8;
  border-radius: 4px;
  background: #f8fafc;
  cursor: pointer;
}
</style>
