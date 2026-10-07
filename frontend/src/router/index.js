import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/projects' },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: { title: '仪表盘', icon: 'DataBoard' },
  },
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
      { path: 'ai-review', name: 'AiReview', component: () => import('@/views/AiCaseReview.vue') },
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
  {
    path: '/metrics',
    name: 'MetricsOverview',
    component: () => import('@/views/MetricsOverview.vue'),
    meta: { title: 'KPI 指标', icon: 'TrendCharts' },
  },
  {
    path: '/schedules',
    name: 'ScheduleManage',
    component: () => import('@/views/ScheduleManage.vue'),
    meta: { title: '定时执行', icon: 'Timer' },
  },
  {
    path: '/pipelines',
    name: 'PipelineManage',
    component: () => import('@/views/PipelineManage.vue'),
    meta: { title: 'CI/CD 流水线', icon: 'Promotion' },
  },
  {
    path: '/pipelines/:id/runs',
    name: 'PipelineRuns',
    component: () => import('@/views/PipelineRuns.vue'),
    meta: { title: '流水线运行', icon: 'Promotion' },
  },
  {
    path: '/mock',
    name: 'MockManage',
    component: () => import('@/views/MockManage.vue'),
    meta: { title: 'Mock 服务', icon: 'Connection' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/projects' },
]

const router = createRouter({ history: createWebHistory(), routes })
export default router
