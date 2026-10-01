// 检查 QQScope.html 内每个 <script> 块的语法
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf-8");
const re = /<script>([\s\S]*?)<\/script>/g;
let m, i = 0, allOk = true;
while ((m = re.exec(html)) !== null) {
  i++;
  const body = m[1];
  try {
    new Function(body);
    console.log(`块 ${i}: 语法 OK (${(body.length / 1024).toFixed(0)} KB)`);
  } catch (e) {
    allOk = false;
    console.log(`块 ${i}: 语法错误 → ${e.message}`);
    // 定位错误附近
    const m2 = e.message.match(/position (\d+)/);
    if (m2) {
      const pos = +m2[1];
      console.log(`  上下文: ...${body.slice(Math.max(0, pos - 80), pos + 40)}...`);
    }
  }
}
console.log(`共 ${i} 个脚本块, ${allOk ? "全部通过" : "存在错误"}`);
