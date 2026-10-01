"""全量补下载缺失媒体（循环调用 backfill 接口，直到补完或达到上限）
用法：python scripts/media_backfill_all.py [--account 1605289411] [--batch 500] [--max-hours 16]
"""
import argparse, json, sys, time
sys.stdout.reconfigure(encoding='utf-8')
import httpx

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--account', type=int, default=1605289411)
    ap.add_argument('--batch', type=int, default=500)
    ap.add_argument('--base', default='http://127.0.0.1:15555')
    ap.add_argument('--max-hours', type=float, default=16)
    a = ap.parse_args()
    c = httpx.Client(timeout=120, trust_env=False)
    t0 = time.time()
    total_ok = total_fail = 0
    round_no = 0
    while (time.time() - t0) / 3600 < a.max_hours:
        round_no += 1
        try:
            r = c.post(f'{a.base}/api/media/backfill',
                       json={'account_qq': a.account, 'kinds': ['image'], 'limit': a.batch})
            if r.status_code == 409:
                print(f'[轮 {round_no}] 已有任务在跑，等 20s'); time.sleep(20); continue
            j = r.json()
            job = j.get('job')
            if not job:
                print(f'[轮 {round_no}] 启动失败: {r.text[:150]}'); time.sleep(30); continue
            print(f'[轮 {round_no}] 启动 job={job} batch={a.batch}', flush=True)
            ok = fl = 0
            round_t0 = time.time()
            while True:
                time.sleep(15)
                if time.time() - round_t0 > 1800:
                    print('   -> 单轮超 30 分钟，强制结束本轮', flush=True)
                    try: c.post(f'{a.base}/api/media/backfill/stop', params={'job': job})
                    except Exception: pass
                    break
                s = c.get(f'{a.base}/api/media/backfill/status', params={'job': job}).json()
                st = s.get('status')
                if not st:
                    # 后端重启导致内存里的 job 状态丢失 —— 重新起一个，别死等
                    print('   -> job 状态丢失（后端重启过），重新开始一轮', flush=True)
                    break
                if st in ('done', 'stopped', 'error'):
                    ok = s.get('success') or 0; fl = s.get('failed') or 0
                    total_ok += ok; total_fail += fl
                    el = s.get('elapsed') or 0
                    rate = ok / el if el else 0
                    print(f'   -> {st} 成功 {ok} 失败 {fl} 用时 {el:.0f}s ({rate:.2f} 张/秒) 累计 {total_ok}/{total_ok+total_fail}', flush=True)
                    break
                if (time.time() - t0) / 3600 > a.max_hours:
                    c.post(f'{a.base}/api/media/backfill/stop', params={'job': job})
                    break
            if ok == 0 and fl == 0:
                print('   -> 本轮没有可补的条目，结束'); break
        except Exception as e:
            print(f'[轮 {round_no}] 异常 {type(e).__name__}: {e}'); time.sleep(30)
    print(f'=== 结束：累计成功 {total_ok} 失败 {total_fail} ===')

if __name__ == '__main__':
    main()
