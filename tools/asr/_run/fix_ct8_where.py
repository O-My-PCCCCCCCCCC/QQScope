# -*- coding: utf-8 -*-
import io
p = r"E:\01-项目\QQScope\tools\asr\fix_ct8_labels.py"
s = io.open(p, encoding="utf-8").read()
OLD_EXP = ('        "SELECT COUNT(*) n FROM messages WHERE id IN (%s) AND account_qq=? "\n'
           '        "AND kind=\'card\' AND json_extract(media,\'$.kind\')=\'card\' "\n')
NEW_EXP = ('        # 注意：messages.kind 是 c2c/group；名片类型在 media JSON 里（$.kind=\'card\'）。\n'
           '        "SELECT COUNT(*) n FROM messages WHERE id IN (%s) AND account_qq=? "\n'
           '        "AND json_extract(media,\'$.kind\')=\'card\' "\n')
assert OLD_EXP in s
s = s.replace(OLD_EXP, NEW_EXP, 1)
OLD_UPD = ('        sql = ("UPDATE messages SET media=? WHERE id=? AND account_qq=? AND kind=\'card\' "\n'
           '               "AND json_extract(media,\'$.kind\')=\'card\' "\n')
NEW_UPD = ('        sql = ("UPDATE messages SET media=? WHERE id=? AND account_qq=? "\n'
           '               "AND json_extract(media,\'$.kind\')=\'card\' "\n')
assert OLD_UPD in s
s = s.replace(OLD_UPD, NEW_UPD, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("fixed WHERE; kind='card' occurrences left:", s.count("kind='card'"))
