"""Quick import-only test for merge format"""
import httpx, json, io, time
from openpyxl import Workbook

BASE = 'http://127.0.0.1:8000/api/v1'

# Create project
r = httpx.post(f'{BASE}/projects/', json={'name': 'MergeTest', 'target_url': 'https://example.com'})
pid = r.json()['data']['id']
print(f'Project: {pid}')

# ═══ Merge format ═══
wb = Workbook()
ws = wb.active
ws.append(['用例名', '操作', '对象', '数据', '期望'])
ws.append(['注册新用户', 'fill', '用户名输入框', 'newuser', '注册成功提示'])
ws.append(['同意协议', 'click', '同意复选框', '', '协议已勾选'])
buf = io.BytesIO()
wb.save(buf)

r = httpx.post(f'{BASE}/projects/{pid}/cases/import',
    files={'file': ('merge.xlsx', buf.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
d = r.json()['data']
print(f'Merge import: total={d["total"]} success={d["success"]} failed={d["failed"]}')
for e in d.get('errors', []):
    print(f'  ERR: {e}')

# List
r = httpx.get(f'{BASE}/projects/{pid}/cases/')
d = r.json()['data']
print(f'List: {d["total"]} cases')
for it in d['items']:
    steps = it.get('steps', [])
    print(f'  #{it["id"]} {it["case_name"]} steps: {json.dumps(steps, ensure_ascii=False)}')

# Detail
cid = d['items'][0]['id']
r = httpx.get(f'{BASE}/projects/{pid}/cases/{cid}')
detail = r.json()['data']
print(f'\nDetail #{cid} {detail["case_name"]}:')
print(json.dumps(detail['steps'], ensure_ascii=False, indent=2))

# Cleanup
r = httpx.request('DELETE', f'{BASE}/projects/{pid}/cases/', json={'ids': [i['id'] for i in d['items']]})
r = httpx.delete(f'{BASE}/projects/{pid}')
print('Cleaned up')
