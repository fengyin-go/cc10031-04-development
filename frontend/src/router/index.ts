import { createRouter, createWebHistory } from 'vue-router'

import BatchIntegrityView from '@/views/BatchIntegrityView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径必须放在 /:moduleKey 之前
    {
      path: '/batch_tracker/integrity',
      name: 'batch-integrity',
      component: BatchIntegrityView,
      props: { moduleKey: 'batch_tracker' },
    },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
