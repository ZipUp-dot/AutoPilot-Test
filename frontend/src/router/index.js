import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/projects' },
  {
    path: '/projects',
    name: 'ProjectList',
    component: () => import('@/views/ProjectList.vue'),
    meta: { title: '项目管理', icon: 'Folder' },
  },
  {
    path: '/projects/:id',
    component: () => import('@/views/ProjectDetail.vue'),
    meta: { title: '项目详情', icon: 'FolderOpened' },
    children: [
      { path: '', redirect: to => `/projects/${to.params.id}/elements` },
      { path: 'elements', name: 'Elements', component: () => import('@/views/project/ElementCapture.vue') },
      { path: 'cases', name: 'Cases', component: () => import('@/views/project/CaseManagement.vue') },
      { path: 'executions', name: 'Executions', component: () => import('@/views/project/ExecutionPanel.vue') },
      { path: 'reports', name: 'Reports', component: () => import('@/views/project/ReportViewer.vue') },
    ],
  },
  {
    path: '/executions/:executionId',
    name: 'ExecutionDetail',
    component: () => import('@/views/ExecutionDetail.vue'),
    meta: { title: '执行详情', icon: 'VideoPlay' },
  },
  {
    path: '/reports',
    name: 'ReportCenter',
    component: () => import('@/views/ReportCenter.vue'),
    meta: { title: '报告中心', icon: 'Document' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/projects' },
]

const router = createRouter({ history: createWebHistory(), routes })
export default router
