# -*- coding: utf-8 -*-
import io
p = r"E:\01-项目\QQScope\core\voice.py"
s = io.open(p, encoding="utf-8").read()
OLD = '''    where = ("account_qq=? AND json_extract(media,'$.kind')='voice' "
             "AND json_extract(media,'$.file') IS NOT NULL")'''
NEW = '''    # 注意：不要把 file IS NULL 的行排除掉，否则「本地文件缺失」的语音永远进不了
    # pending_jobs -> 永远不被标记为 missing -> /api/voice/stats 的 pending 永远清零不了。
    # 这类行会在 _run_locked 里走 missing 分支，如实记 voice_status='missing'。
    where = "account_qq=? AND json_extract(media,'$.kind')='voice'"'''
assert OLD in s, "pending_jobs where not found"
s = s.replace(OLD, NEW, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("patched core/voice.py")
