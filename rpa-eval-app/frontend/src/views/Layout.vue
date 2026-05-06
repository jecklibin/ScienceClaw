<template>
  <el-container class="layout-shell">
    <el-aside width="216px" class="side">
      <div class="brand">
        <strong>采购合规RPA</strong>
        <span>企业评估环境</span>
      </div>
      <el-menu router :default-active="$route.path" class="menu">
        <el-menu-item index="/dashboard">工作台</el-menu-item>
        <el-menu-item index="/contracts">合同台账</el-menu-item>
        <el-menu-item index="/suppliers">供应商台账</el-menu-item>
        <el-menu-item index="/purchase-requests">采购申请</el-menu-item>
        <el-menu-item index="/purchase-orders">采购订单</el-menu-item>
        <el-menu-item index="/approvals">审批待办</el-menu-item>
        <el-menu-item index="/reports">报表中心</el-menu-item>
        <el-menu-item index="/regression-lab">Regression Lab</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <div>
          <strong>{{ routeTitle }}</strong>
          <span class="muted">采购、合同与合规流程自动化评估</span>
        </div>
        <div class="user-box">
          <el-tag type="info">{{ auth.roleLabel }}</el-tag>
          <span>{{ auth.user?.display_name || auth.user?.username }}</span>
          <el-button data-testid="logout-button" @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main>
        <RouterView />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const titles: Record<string, string> = {
  '/dashboard': '工作台',
  '/contracts': '合同台账',
  '/suppliers': '供应商台账',
  '/purchase-requests': '采购申请',
  '/purchase-orders': '采购订单',
  '/approvals': '审批待办',
  '/reports': '报表中心',
  '/regression-lab': 'Regression Lab',
  '/regression-lab/popup-report': 'Popup Report'
}
const routeTitle = computed(() => titles[route.path] || '业务详情')

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.layout-shell {
  min-height: 100vh;
}

.side {
  background: #1f2a37;
  color: #fff;
}

.brand {
  height: 64px;
  padding: 14px 16px;
  border-bottom: 1px solid rgb(255 255 255 / 12%);
}

.brand span {
  display: block;
  margin-top: 4px;
  color: #b9c2d0;
  font-size: 12px;
}

.menu {
  border-right: 0;
  background: transparent;
}

:deep(.el-menu-item) {
  color: #d7deea;
}

:deep(.el-menu-item.is-active),
:deep(.el-menu-item:hover) {
  color: #fff;
  background: #2f81f7;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #d9e0ea;
}

.topbar strong {
  margin-right: 12px;
}

.user-box {
  display: flex;
  gap: 10px;
  align-items: center;
}

.el-main {
  padding: 0;
}
</style>
