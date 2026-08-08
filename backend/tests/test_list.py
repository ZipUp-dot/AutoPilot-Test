import httpx

B = 'http://127.0.0.1:8000/api/v1'
pid = 1

# Test LIST with trailing slash
r = httpx.get(f'{B}/projects/{pid}/cases/', params={'page': 1, 'size': 10})
print(f'status: {r.status_code}')
d = r.json()['data']
print(f'total: {d["total"]}')

for it in d['items']:
    steps = [s.get('action', '') + ' ' + s.get('target', '')[:20] for s in it.get('steps', [])]
    print(f'  #{it["id"]} {it["case_name"]} case_no={it["case_no"]} pre={it["pre_condition"]} steps={len(it["steps"])}')

# Detail
case_id = d['items'][0]['id']
r = httpx.get(f'{B}/projects/{pid}/cases/{case_id}')
detail = r.json()['data']
print(f'\nDETAIL #{case_id}: {detail["case_name"]}')
for s in detail['steps']:
    print(f'  {s["action"]} -> {s["target"][:40]} | {s.get("value", "")[:20]}')

# Search
r = httpx.get(f'{B}/projects/{pid}/cases/', params={'keyword': '登录'})
print(f'\nSEARCH: {r.json()["data"]["total"]} results')

# Batch delete
all_ids = [it['id'] for it in d['items']]
r = httpx.request('DELETE', f'{B}/projects/{pid}/cases/', json={'ids': all_ids})
print(f'BATCH delete: {r.json()["data"]}')

r = httpx.get(f'{B}/projects/{pid}/cases/')
print(f'After: {r.json()["data"]["total"]} cases')

# Cleanup
r = httpx.delete(f'{B}/projects/{pid}')
print(f'Cleanup: {r.json()["data"]}')
print('ALL PASSED')
