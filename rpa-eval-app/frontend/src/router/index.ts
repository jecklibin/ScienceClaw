import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/Login.vue') },
    {
      path: '/',
      component: () => import('@/views/Layout.vue'),
      meta: { requiresAuth: true },
      children: [
        { path: '', redirect: '/dashboard' },
        { path: 'dashboard', name: 'dashboard', component: () => import('@/views/Dashboard.vue') },
        { path: 'contracts', name: 'contracts', component: () => import('@/views/Contracts.vue') },
        { path: 'contracts/:number', name: 'contract-detail', component: () => import('@/views/ContractDetail.vue') },
        { path: 'suppliers', name: 'suppliers', component: () => import('@/views/Suppliers.vue') },
        { path: 'purchase-requests', name: 'purchase-requests', component: () => import('@/views/PurchaseRequests.vue') },
        { path: 'purchase-requests/new', name: 'purchase-request-form', component: () => import('@/views/PurchaseRequestForm.vue') },
        { path: 'purchase-orders', name: 'purchase-orders', component: () => import('@/views/PurchaseOrders.vue') },
        { path: 'approvals', name: 'approvals', component: () => import('@/views/Approvals.vue') },
        { path: 'reports', name: 'reports', component: () => import('@/views/Reports.vue') },
        { path: 'regression-lab', name: 'regression-lab', component: () => import('@/views/RegressionLab.vue') },
        { path: 'regression-lab/popup-report', name: 'regression-popup-report', component: () => import('@/views/PopupReport.vue') }
      ]
    }
  ]
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAuth && auth.isAuthenticated && !auth.user) {
    try {
      await auth.fetchMe()
    } catch {
      auth.logout()
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }
  if (to.path === '/login' && auth.isAuthenticated) {
    return '/dashboard'
  }
})

export default router
