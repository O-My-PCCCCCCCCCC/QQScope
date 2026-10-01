/* QQScope · 情绪分析引擎 v2 测试 + 旧/新算法真实数据对比
 * 运行：node scripts/test_analysis.js
 * 退出码：0 = 全部单测通过；1 = 有单测失败
 *
 * 内容：
 *   1) 30+ 条单元测试（否定翻转 / 程度加权 / 网络用语 / 行为信号 / 关怀词 / 聚合）
 *   2) v1 旧算法基线（内嵌 v1 词典与打分逻辑，只用于对比，不代表当前引擎）
 *   3) 真实数据对比：优先取后端主库 /api/report（自己发的 + 有文本），
 *      后端没起则退回 data/pack/<qq>/messages.json.gz
 */
"use strict";
const fs = require("fs");
const zlib = require("zlib");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const engine = require(path.join(ROOT, "app", "js", "analysis.js"));
const score = engine.scoreMessage;
const computeReport = engine.computeReport;

let passed = 0, failed = 0;
const failedNames = [];
function ok(name, cond, extra) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; failedNames.push(name); console.log("  FAIL  " + name + (extra !== undefined ? "  -> " + extra : "")); }
}
function abs(v) { return Math.abs(v); }
function dayKey(ts) {
  const d = new Date(ts * 1000);
  const p = (n) => (n < 10 ? "0" + n : "" + n);
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

/* ===================== 1. 单元测试 ===================== */
console.log("===== 1. 单元测试 =====");

ok("空文本：score=0 confidence=0 care=false",
  (() => { const r = score(""); return r.score === 0 && r.confidence === 0 && r.care === false; })());
ok("纯空白：score=0 confidence=0",
  (() => { const r = score("   \n\t"); return r.score === 0 && r.confidence === 0; })());
ok("纯积极 emoji 😄😊：score>0 且 confidence>0",
  (() => { const r = score("😄😊"); return r.score > 0 && r.confidence > 0; })());
ok("纯消极 emoji 😭😡：score<0 且 confidence>0",
  (() => { const r = score("😭😡"); return r.score < 0 && r.confidence > 0; })());
ok("基础积极词「开心」：score>0",
  score("开心").score > 0);
ok("否定翻转「不开心」：score<0（v1 会当积极）",
  score("不开心").score < 0, "score=" + score("不开心").score.toFixed(3));
ok("否定翻转「不高兴」：score<0",
  score("不高兴").score < 0);
ok("否定翻转「没意思」：score<0",
  score("没意思").score < 0);
ok("否定翻转「没有希望」：score<0",
  score("没有希望").score < 0, "score=" + score("没有希望").score.toFixed(3));
ok("否定翻转消极词「不累」：score>0",
  score("不累").score > 0);
ok("歧义词「差不多」不再被单字「差」误判为消极",
  Math.abs(score("差不多").score) < 0.05);
ok("程度加强：score(很开心) > score(开心)",
  score("很开心").score > score("开心").score,
  score("很开心").score.toFixed(3) + " vs " + score("开心").score.toFixed(3));
ok("程度加强：score(超级开心) > score(开心)",
  score("超级开心").score > score("开心").score);
ok("程度减弱：|score(有点烦)| < |score(烦)|",
  abs(score("有点烦").score) < abs(score("烦").score),
  abs(score("有点烦").score).toFixed(3) + " vs " + abs(score("烦").score).toFixed(3));
ok("程度加强：|score(烦死了)| > |score(烦)|",
  abs(score("烦死了").score) > abs(score("烦").score),
  abs(score("烦死了").score).toFixed(3) + " vs " + abs(score("烦").score).toFixed(3));
ok("网络用语「emo」：score<0",
  score("emo了").score < 0);
ok("网络用语「破防」：score<0",
  score("破防了").score < 0);
ok("网络用语「摆烂」：score<0",
  score("摆烂").score < 0);
ok("网络用语「笑死」：score>0",
  score("笑死我了").score > 0);
ok("网络用语「yyds」：score>0",
  score("yyds").score > 0);
ok("重复标点「烦！！！」：|score| > |score(烦)| 且 confidence 更高",
  (() => { const a = score("烦"), b = score("烦！！！"); return abs(b.score) > abs(a.score) && b.confidence > a.confidence; })());
ok("重复字符「哈哈哈」：score>0",
  score("哈哈哈哈").score > 0);
ok("重复字符「呜呜呜」：score<0",
  score("呜呜呜").score < 0);
ok("关怀词「想死」：care=true score<=-0.8 confidence>=0.9",
  (() => { const r = score("想死"); return r.care === true && r.score <= -0.8 && r.confidence >= 0.9; })(),
  (() => { const r = score("想死"); return "care=" + r.care + " score=" + r.score.toFixed(2) + " conf=" + r.confidence.toFixed(2); })());
ok("关怀词「不想活」：care=true 且不会被否定翻转成正分",
  (() => { const r = score("不想活"); return r.care === true && r.score < 0; })());
ok("极短消息带情绪：|score(烦)| > |score(烦+60 字)|",
  abs(score("烦").score) > abs(score("烦" + "字".repeat(60)).score));
ok("深夜加权：score(难过, 凌晨2点) < score(难过, 下午14点)",
  score("难过", 2).score < score("难过", 14).score,
  score("难过", 2).score.toFixed(3) + " vs " + score("难过", 14).score.toFixed(3));
ok("无信号消息：confidence=0",
  score("今天天气").confidence === 0);
ok("兼容字段：senti===score 且 pos/neg/care 存在",
  (() => { const r = score("开心"); return r.senti === r.score && typeof r.pos === "number" && typeof r.neg === "number" && typeof r.care === "boolean"; })());
ok("词典规模：积极/消极各 >=300（实为 " + engine.lexiconInfo().pos_words + "/" + engine.lexiconInfo().neg_words + "）",
  engine.lexiconInfo().pos_words >= 300 && engine.lexiconInfo().neg_words >= 300);
ok("module.exports 与 global.QQScopeEngine 都存在",
  typeof engine.computeReport === "function" && typeof global.QQScopeEngine.scoreMessage === "function");

/* computeReport 结构 + 聚合测试 */
const DAY = 1786752000; /* 2026-08-15 前后某天，具体不影响 */
function at(h, m, s) { return Math.floor(new Date(2026, 7, 15, h, m == null ? 0 : m, s || 0).getTime() / 1000); }
const synth = [
  { t: at(9), d: 1, p: "u_1", k: "c2c", x: "今天很开心" },
  { t: at(10), d: 1, p: "u_1", k: "c2c", x: "不开心" },
  { t: at(11), d: 1, p: "g_1", k: "group", x: "哈哈哈" },
  { t: at(12), d: 0, p: "u_1", k: "c2c", x: "对方说想死" },   /* 对方消息：不计入自己 */
  { t: at(13), d: 1, p: "u_1", k: "c2c", x: "今天天气" },
  { t: at(2), d: 1, p: "u_1", k: "c2c", x: "烦死了" }
];
const rep = computeReport(synth, { self_qq: 1605289411, total_messages: synth.length });
ok("computeReport 只统计 direction=1（self_messages=5）",
  rep.overview.self_messages === 5, "实际 " + rep.overview.self_messages);
ok("computeReport 结构键齐全",
  ["overview", "sentiment", "daily", "peers", "top_pos_words", "top_neg_words", "night_hours", "hour_hist", "care_words"]
    .every((k) => rep[k] !== undefined));
ok("daily 含 confidence / coverage 字段",
  rep.daily.length > 0 && rep.daily[0].confidence !== undefined && rep.daily[0].coverage !== undefined);
ok("coverage 计算正确：5 条里 4 条有信号 => 0.8",
  rep.daily.length === 1 && abs(rep.daily[0].coverage - 0.8) < 1e-6,
  rep.daily.length ? String(rep.daily[0].coverage) : "no daily");
ok("sentiment.coverage / lexicon_version 存在",
  rep.sentiment.coverage !== undefined && !!rep.sentiment.lexicon_version);

const careDay = [];
for (let i = 0; i < 9; i++) careDay.push({ t: at(10, i), d: 1, p: "u_1", k: "c2c", x: "今天天气" });
careDay.push({ t: at(23, 30), d: 1, p: "u_1", k: "c2c", x: "想死" });
const careRep = computeReport(careDay, { self_qq: 1 });
ok("关怀消息不被平均掉：9 条中性 + 1 条「想死」→ 当天 senti<=-0.3 且 care=1",
  careRep.daily.length === 1 && careRep.daily[0].senti <= -0.3 && careRep.daily[0].care === 1,
  careRep.daily.length ? ("senti=" + careRep.daily[0].senti + " care=" + careRep.daily[0].care) : "no daily");
ok("computeReport 兼容 store 格式 {ts,direction,kind,peer_id,text}",
  computeReport([{ ts: at(10), direction: 1, kind: "c2c", peer_id: "u_9", text: "开心" }], { self_qq: 1 }).overview.self_messages === 1);

/* ===================== 2. v1 旧算法基线 ===================== */
const LEGACY_POS = ["开心","高兴","快乐","幸福","爽","棒","好耶","赞","喜欢","爱","期待","哈哈","嘿嘿","嘻嘻","笑死","不错","厉害","牛","太棒","舒服","轻松","顺利","成功","优秀","满意","感谢","谢谢","耶","好玩","有趣","美","漂亮","好看","好听","满足","幸运","惊喜","感动","温暖","加油","赢","赚","恭喜","真棒","太强","给力","嗨","冲","睡得好","香","甜","甜","爱了","绝","牛批","牛b"];
const LEGACY_NEG = ["烦","烦躁","焦虑","压力","累","好累","心累","难过","伤心","生气","愤怒","讨厌","恶心","无语","自闭","崩溃","绝望","郁闷","痛苦","想哭","哭","泪","难受","不爽","倒霉","糟糕","失败","垃圾","可怕","恐怖","害怕","担心","紧张","不安","寂寞","孤独","空虚","迷茫","失望","烦死","淦","靠","拉胯","废物","废了","废","难受死","气死","炸","裂开","麻了","摆烂","躺平","emo","emo了","玉玉","破防","急了","血压","血压高","离谱","服了","吐了","晦气","背","衰","亏","烦人","讨厌鬼","坑","坑爹"];
const LEGACY_POS_EMOJI = ["😄","😃","😊","😁","😂","🤣","😆","😍","🥰","😘","👍","👏","❤️","🎉","✌️","🥳","🤩"];
const LEGACY_NEG_EMOJI = ["😭","😢","😡","😠","🤬","😞","😔","😣","😫","😩","💔","😤","😰","😨","🥶","🤮"];

function legacyCountHits(text, words) {
  let n = 0;
  for (let i = 0; i < words.length; i++) {
    let idx = text.indexOf(words[i]);
    while (idx !== -1) { n++; idx = text.indexOf(words[i], idx + words[i].length); }
  }
  return n;
}
function legacyScore(text) {
  if (!text) return { pos: 0, neg: 0, senti: 0, signal: false };
  const pos = legacyCountHits(text, LEGACY_POS) + legacyCountHits(text, LEGACY_POS_EMOJI);
  const neg = legacyCountHits(text, LEGACY_NEG) + legacyCountHits(text, LEGACY_NEG_EMOJI);
  const total = pos + neg;
  let senti = total === 0 ? 0 : (pos - neg) / Math.max(total, 1);
  if (pos > 0 && neg === 0) senti = 1;
  else if (neg > 0 && pos === 0) senti = -1;
  return { pos: pos, neg: neg, senti: senti, signal: total > 0 };
}
function legacyReport(messages, meta) {
  const self = (messages || []).filter((m) => m && m.d === 1);
  const days = {};
  let signal = 0;
  self.forEach((m) => {
    const x = m.x || "";
    const ts = m.t || 0;
    if (!ts) return;
    const key = dayKey(ts);
    const s = legacyScore(x);
    const d = days[key] || (days[key] = { date: key, count: 0, sum: 0, pos: 0, neg: 0, night: 0, signal: 0 });
    d.count++; d.sum += s.senti; d.pos += s.pos; d.neg += s.neg;
    if (s.signal) { d.signal++; signal++; }
    const h = new Date(ts * 1000).getHours();
    if (h >= 23 || h < 5) d.night++;
  });
  return {
    signal: signal,
    selfCount: self.length,
    daily: Object.keys(days).sort().map((k) => {
      const d = days[k];
      return { date: d.date, count: d.count, senti: d.count ? d.sum / d.count : 0, pos: d.pos, neg: d.neg, signal: d.signal, coverage: d.count ? d.signal / d.count : 0 };
    })
  };
}

/* ===================== 3. 真实数据 旧 vs 新 ===================== */
async function loadReal() {
  const qq = 1605289411;
  try {
    if (typeof fetch === "function") {
      const ctrl = new AbortController();
      const to = setTimeout(() => ctrl.abort(), 8000);
      const r = await fetch("http://127.0.0.1:15555/api/report?account=" + qq + "&limit=30000", { signal: ctrl.signal });
      clearTimeout(to);
      if (r.ok) {
        const j = await r.json();
        if (j && j.messages && j.messages.length) {
          return { source: "后端主库 /api/report（direction=1 且有文本）", messages: j.messages, meta: j.meta || { self_qq: qq } };
        }
      }
    }
  } catch (e) { /* 后端没起，退回数据包 */ }
  const gz = path.join(ROOT, "data", "pack", String(qq), "messages.json.gz");
  if (!fs.existsSync(gz)) return { source: "无可用真实数据", messages: [], meta: {} };
  const all = JSON.parse(zlib.gunzipSync(fs.readFileSync(gz)).toString("utf-8"));
  let meta = {};
  try { meta = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "pack", String(qq), "meta.json"), "utf-8")); } catch (e) {}
  return { source: "数据包 data/pack/" + qq, messages: all.filter((m) => m.d === 1), meta: Object.assign({ self_qq: qq }, meta) };
}

function pad(s, n) { s = String(s); while (s.length < n) s += " "; return s; }
function padL(s, n) { s = String(s); while (s.length < n) s = " " + s; return s; }

(async function main() {
  const real = await loadReal();
  const self = real.messages.filter((m) => m && m.d === 1);
  console.log("\n===== 2. 真实数据 旧算法 vs 新算法 =====");
  console.log("数据源: " + real.source);
  console.log("自己发出（有文本）消息: " + self.length + " 条");
  if (!self.length) {
    console.log("(没有真实数据，跳过对比)");
  } else {
    const newRep = computeReport(self, real.meta);
    const oldRep = legacyReport(self, real.meta);
    const newSig = newRep.sentiment.signal_messages;
    const oldSig = oldRep.signal;
    console.log("");
    console.log("【有信号消息数】旧: " + oldSig + " (" + (100 * oldSig / self.length).toFixed(1) + "%)  ->  新: " + newSig + " (" + (100 * newSig / self.length).toFixed(1) + "%)");
    console.log("【新 coverage】" + newRep.sentiment.coverage + " · 平均 confidence " + newRep.sentiment.avg_confidence + " · lexicon " + newRep.sentiment.lexicon_version);
    console.log("【新平均情绪分】" + newRep.sentiment.avg_senti + "（旧: " + (oldRep.daily.reduce((a, d) => a + d.senti * d.count, 0) / Math.max(1, self.length)).toFixed(3) + "）");

    /* 5 天曲线对比：取消息量最多的 5 天 */
    const newDays = newRep.daily.slice().sort((a, b) => b.count - a.count).slice(0, 5).sort((a, b) => a.date < b.date ? -1 : 1);
    const oldMap = {};
    oldRep.daily.forEach((d) => { oldMap[d.date] = d; });
    console.log("");
    console.log("【5 天曲线对比（按消息量取 Top5 天）】");
    console.log("  " + pad("日期", 12) + padL("消息", 6) + padL("旧senti", 9) + padL("新senti", 9) + padL("新coverage", 12) + padL("旧命中率", 10));
    newDays.forEach((d) => {
      const o = oldMap[d.date] || { senti: 0, coverage: 0 };
      console.log("  " + pad(d.date, 12) + padL(d.count, 6) + padL(o.senti.toFixed(3), 9) + padL(d.senti.toFixed(3), 9) + padL(d.coverage.toFixed(3), 12) + padL(o.coverage.toFixed(3), 10));
    });

    /* 差异最大的 天 × 类型 */
    const cells = {};
    self.forEach((m) => {
      const ts = m.t, dk = dayKey(ts), kind = m.k || "c2c", key = dk + "|" + kind, x = m.x || "";
      const o = legacyScore(x);
      const nw = score(x, new Date(ts * 1000).getHours());
      const c = cells[key] || (cells[key] = { date: dk, kind: kind, count: 0, oldSum: 0, newW: 0, newSentiW: 0, oldSig: 0, newSig: 0, words: {} });
      c.count++; c.oldSum += o.senti;
      if (o.signal) c.oldSig++;
      if (nw.confidence > 0) { c.newSig++; c.newW += nw.confidence; c.newSentiW += nw.score * nw.confidence; }
      nw.pos_words.concat(nw.neg_words).forEach((w) => { c.words[w] = (c.words[w] || 0) + 1; });
    });
    Object.keys(cells).forEach((k) => {
      const c = cells[k];
      const oldAvg = c.oldSum / c.count;
      const newAvg = c.newW ? c.newSentiW / c.newW : 0;
      c.oldAvg = oldAvg; c.newAvg = newAvg; c.diff = Math.abs(oldAvg - newAvg);
    });
    const allCells = Object.keys(cells).map((k) => cells[k]);
    const bigCells = allCells.filter((c) => c.count >= 20);
    const pool = bigCells.length ? bigCells : allCells.filter((c) => c.count >= 5);
    let maxCell = null;
    pool.forEach((c) => { if (!maxCell || c.diff > maxCell.diff) maxCell = c; });
    if (pool.length > 1) {
      const top3 = pool.slice().sort((a, b) => b.diff - a.diff).slice(0, 3);
      console.log("");
      console.log("【差异 Top3（消息数 " + (bigCells.length ? ">=20" : ">=5") + " 的天×类型）】");
      top3.forEach((c) => console.log("  " + c.date + " · " + (c.kind === "group" ? "群聊" : "私聊") + "（" + c.count + " 条）旧 " +
        c.oldAvg.toFixed(3) + " -> 新 " + c.newAvg.toFixed(3) + "（差异 " + c.diff.toFixed(3) + "，新命中率 " + (100 * c.newSig / c.count).toFixed(1) + "%）"));
    }
    if (maxCell) {
      console.log("");
      console.log("【差异最大的 天 × 类型】" + maxCell.date + " · " + (maxCell.kind === "group" ? "群聊" : "私聊") +
        "（" + maxCell.count + " 条）旧 " + maxCell.oldAvg.toFixed(3) + " -> 新 " + maxCell.newAvg.toFixed(3) +
        "（差异 " + maxCell.diff.toFixed(3) + "，新命中率 " + (100 * maxCell.newSig / maxCell.count).toFixed(1) + "%）");
      const topWords = Object.keys(maxCell.words).sort((a, b) => maxCell.words[b] - maxCell.words[a]).slice(0, 8);
      console.log("  当天该类新增识别到的情绪词/网梗: " + topWords.map((w) => w + "×" + maxCell.words[w]).join("，"));
      const samples = self.filter((m) => m.d === 1 && dayKey(m.t) === maxCell.date && (m.k || "c2c") === maxCell.kind)
        .map((m) => ({ x: m.x || "", o: legacyScore(m.x || "").senti, n: score(m.x || "", new Date(m.t * 1000).getHours()) }))
        .filter((e) => e.o === 0 && Math.abs(e.n.score) > 0.05)
        .slice(0, 5);
      if (samples.length) {
        console.log("  旧算法漏判、新算法识别到的例子:");
        samples.forEach((e) => console.log("    「" + e.x.slice(0, 40) + "」 旧 0.00 -> 新 " + e.n.score.toFixed(2) + " (conf " + e.n.confidence.toFixed(2) + ")"));
      }
    }
  }

  console.log("\n===== 3. 当前引擎报告摘要（回归，node scripts/test_analysis.js 可跑通） =====");
  const sample = self.length ? computeReport(self, real.meta) : computeReport([], {});
  console.log("  自发消息 " + sample.overview.self_messages + " 条 / 活跃 " + sample.overview.active_days + " 天 / 日均 " + sample.overview.avg_per_day +
    " / 深夜占比 " + sample.overview.night_ratio + "% / coverage " + sample.overview.coverage);
  console.log("  情绪: 积极命中 " + sample.sentiment.pos_hits + " / 消极命中 " + sample.sentiment.neg_hits + " / 平均情绪分 " + sample.sentiment.avg_senti +
    " / 关怀消息 " + sample.sentiment.care_msgs);
  console.log("  高频积极词: " + sample.top_pos_words.map((w) => w.w + "×" + w.c).join("，"));
  console.log("  高频消极词: " + sample.top_neg_words.map((w) => w.w + "×" + w.c).join("，"));

  console.log("\n===== 单元测试结果: " + passed + " 通过 / " + failed + " 失败 =====");
  if (failed) { console.log("失败用例: " + failedNames.join("；")); }
  process.exit(failed ? 1 : 0);
})();