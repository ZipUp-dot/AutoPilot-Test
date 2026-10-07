/**
 * EXT-AITC-10B 节拍 1（RED 先行）· AC-06 零浏览器副作用（8.7）
 *
 * 审核页只读展示证据：禁止任何浏览器自动化动作 / DOM 副作用 /
 * 绕过 api 封装的网络调用。evidence_ref 仅做名称匹配（纯字符串）。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const _here = dirname(fileURLToPath(import.meta.url))
const SRC = resolve(_here, '../../src')

const FEATURE_FILES = [
  'api/aiDrafts.js',
  'stores/aiDrafts.js',
  'views/AiCaseReview.vue',
  'components/DraftStepEditor.vue',
]

const FORBIDDEN = [
  { name: '浏览器自动化动作', re: /\bpage\.(click|fill|goto|type|press|check|selectOption|hover)\s*\(/ },
  { name: '表单 submit', re: /\.submit\s*\(/ },
  { name: 'DOM 事件派发', re: /\b(dispatchEvent|fireEvent)\s*\(/ },
  { name: '直接 DOM 访问', re: /\bdocument\.(querySelector|getElementById|createElement|execCommand)/ },
  { name: '绕过 api 封装的网络调用', re: /\b(fetch|XMLHttpRequest)\s*\(/ },
]

describe('AC-06 · 审核页零浏览器副作用', () => {
  it.each(FEATURE_FILES)('%s 不含副作用调用', (rel) => {
    const src = readFileSync(resolve(SRC, rel), 'utf8')
    const hits = FORBIDDEN.filter(f => f.re.test(src)).map(f => f.name)
    expect(hits).toEqual([])
  })
})
