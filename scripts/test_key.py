"""QQScope · 排查 nt_msg.db 解密参数/密钥
用法：uv run --project <nt_msg_db_util> python scripts/test_key.py <db路径> <key>
尝试多种 SQLCipher 参数组合，找出能打开库的组合。
"""
import sys
import sqlcipher3.dbapi2 as sc

DB = sys.argv[1] if len(sys.argv) > 1 else "nt_msg_clear.db"
KEY = sys.argv[2] if len(sys.argv) > 2 else "uRuTnBuE.mWZDFWx"

PAGE_SIZES = [1024, 2048, 4096, 8192]
KDF_ITERS = [4000, 256000, 64000, 1]
HMACS = ["HMAC_SHA1", "HMAC_SHA512"]
KDFS = ["PBKDF2_HMAC_SHA512", "PBKDF2_HMAC_SHA1"]

print(f"测试库: {DB}  密钥: {KEY} ({len(KEY)}字节)")
found = []
for ps in PAGE_SIZES:
    for ki in KDF_ITERS:
        for hm in HMACS:
            for kdf in KDFS:
                try:
                    con = sc.connect(DB)
                    cur = con.cursor()
                    cur.execute(f"PRAGMA cipher_page_size = {ps}")
                    cur.execute(f"PRAGMA key = '{KEY}'")
                    cur.execute(f"PRAGMA kdf_iter = {ki}")
                    cur.execute(f"PRAGMA cipher_hmac_algorithm = {hm}")
                    cur.execute(f"PRAGMA cipher_kdf_algorithm = {kdf}")
                    cur.execute("SELECT count(*) FROM sqlite_master")
                    n = cur.fetchone()[0]
                    print(f"  ✓ page={ps} iter={ki} hmac={hm} kdf={kdf} → sqlite_master 行数={n}")
                    found.append((ps, ki, hm, kdf, n))
                    con.close()
                    break  # 找到就换下一个 iter
                except Exception as e:
                    pass
            if found and found[-1][0] == ps and found[-1][1] == ki:
                break
        if found and found[-1][0] == ps and found[-1][1] == ki:
            break

if found:
    print("\n成功组合: " + str(found[0]))
else:
    print("\n所有组合均失败 —— 密钥或库本身不匹配（可能密钥分账号）")
