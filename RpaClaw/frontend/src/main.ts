import { createApp, h } from 'vue'
import { createRouter, createWebHistory, RouterView } from 'vue-router'
import App from './App.vue'
import './assets/global.css'
import './assets/theme.css'
import 'highlight.js/styles/github-dark.css'
import 'katex/dist/katex.min.css'
import './utils/toast'
import i18n from './composables/useI18n'
import { getStoredToken, getCachedAuthProvider } from './api/auth'
import { initSandboxConfig } from './utils/sandbox'

// Import page components
import HomePage from './pages/HomePage.vue'
import ChatPage from './pages/ChatPage.vue'
import SkillsPage from './pages/SkillsPage.vue'
import SkillDetailPage from '@/pages/SkillDetailPage.vue'
import ToolsPage from './pages/ToolsPage.vue'
import ToolDetailPage from './pages/ToolDetailPage.vue'
import TasksPage from './pages/TasksPage.vue'
import LoginPage from './pages/LoginPage.vue'
import MainLayout from './pages/MainLayout.vue'
import CredentialsPage from './pages/CredentialsPage.vue'
import RecorderPage from './pages/rpa/RecorderPage.vue'
import ConfigurePage from './pages/rpa/ConfigurePage.vue'
import TestPage from './pages/rpa/TestPage.vue'
import McpConvertPage from './pages/rpa/McpConvertPage.vue'
import McpToolEditorPage from './pages/tools/McpToolEditorPage.vue'
import { configure } from "vue-gtag";

configure({
  tagId: 'G-XCRZ3HH31S' // Replace with your own Google Analytics tag ID
})

// Create router
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/chat',
      component: MainLayout,
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          component: HomePage,
          alias: ['/', '/home'],
          meta: { requiresAuth: true }
        },
        {
          path: ':sessionId',
          component: ChatPage,
          meta: { requiresAuth: true }
        },
        {
          path: 'skills',
          component: SkillsPage,
          meta: { requiresAuth: true }
        },
        {
          path: 'skills/:skillName',
          component: SkillDetailPage,
          meta: { requiresAuth: true }
        },
        {
          path: 'tools',
          component: ToolsPage,
          meta: { requiresAuth: true }
        },
        {
          path: 'tools/mcp/new',
          component: McpToolEditorPage,
          meta: { requiresAuth: true }
        },
        {
          path: 'tools/mcp/:toolId',
          component: McpToolEditorPage,
          meta: { requiresAuth: true }
        },
        {
          path: 'tools/:toolName',
          component: ToolDetailPage,
          meta: { requiresAuth: true }
        },
        {
          path: 'tasks',
          component: TasksPage,
          meta: { requiresAuth: true }
        },
        {
          path: 'credentials',
          component: CredentialsPage,
          meta: { requiresAuth: true }
        }
      ]
    },
    {
      path: '/rpa',
      component: { render: () => h(RouterView) },
      meta: { requiresAuth: true },
      children: [
        {
          path: 'recorder',
          component: RecorderPage,
        },
        {
          path: 'configure',
          component: ConfigurePage,
        },
        {
          path: 'test',
          component: TestPage,
        },
        {
          path: 'convert-mcp',
          component: McpConvertPage,
        }
      ]
    },
    {
      path: '/login',
      component: LoginPage
    }
  ]
})

// Global route guard
router.beforeEach(async (to, _, next) => {
  const requiresAuth = to.matched.some((record: any) => record.meta?.requiresAuth)
  const hasToken = !!getStoredToken()

  if (requiresAuth) {
    const authProvider = await getCachedAuthProvider()

    if (authProvider === 'none') {
      next()
      return
    }

    if (!hasToken) {
      next({
        path: '/login',
        query: { redirect: to.fullPath }
      })
      return
    }
  }

  if (to.path === '/login' && hasToken) {
    next('/')
  } else {
    next()
  }
})

const app = createApp(App)

app.use(router)
app.use(i18n)
app.mount('#app')

// Prime sandbox config cache (non-blocking)
initSandboxConfig()
