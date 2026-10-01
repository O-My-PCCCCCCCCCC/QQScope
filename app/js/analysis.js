/* QQScope · 情绪分析引擎 v2（纯 JS，零依赖）
 * 输入：messages 数组 [{t,d,p,k,x}]（兼容 {ts,direction,kind,peer_id,text}） + meta
 * 输出：report 对象（供 UI 渲染 / Node 调试打印）
 *
 * v2 相对 v1 的变化（v1 只数命中次数）：
 *   1) 词典大幅扩充：积极 / 消极各 300+，新增关怀/风险词一档
 *   2) 否定翻转：情绪词前 3 字内出现 不/没/没有/别/无/非/未/毫无 → 翻转极性
 *   3) 程度副词：很/超/太/非常/特别/巨/贼/超级/极其/十分/相当/格外/尤其/真/真的/死了/死/爆/炸 → ×1.5
 *                有点/有些/一点/稍微/稍稍/略/略微/还算/勉强 → ×0.5
 *   4) 行为信号：重复标点（！！/？？）、重复字符（哈哈哈/呜呜呜）、消息长度、深夜(23-5)负面 ×1.3
 *   5) 每条消息输出 score∈[-1,1] + confidence∈[0,1]
 *   6) 日聚合按 confidence 加权平均，并输出 coverage（当天有信号消息占比）
 *
 * 词典来源说明：以下词表为本项目自建（常见中文情绪词 + 口语 + 网络流行语 + emoji），
 * 由人工整理，未直接引用某个学术情感词典；如需学术口径请另行标注来源。
 * 本引擎只统计 direction===1（自己发出的）的消息；对方消息仅作互动背景统计。
 */
(function (global) {
  "use strict";

  var LEXICON_VERSION = "v2-2026.10-selfbuilt";

  /* ==================== 一、积极词（≥300） ==================== */
  var POS_WORDS = [
    /* 基础情绪 */
    "开心", "高兴", "快乐", "幸福", "愉快", "愉悦", "欢喜", "喜悦", "兴奋", "满足",
    "满意", "舒服", "舒坦", "舒适", "轻松", "惬意", "安心", "安逸", "踏实", "平静",
    "宁静", "放松", "自在", "畅快", "痛快", "爽", "赞", "棒", "好", "不错",
    "优秀", "厉害", "强", "顶", "给力", "靠谱", "完美", "精彩", "出色", "卓越",
    "成功", "顺利", "顺心", "如意", "幸运", "走运", "福气", "吉利", "赢", "赚",
    "发财", "中奖", "惊喜", "感动", "温暖", "温馨", "甜蜜", "甜", "美好", "美妙",
    "漂亮", "好看", "好听", "好玩", "有趣", "有意思", "好笑", "逗", "萌", "可爱",
    "治愈", "感恩", "感谢", "谢谢", "多谢", "感激", "知足", "欣慰", "骄傲", "自豪",
    "自信", "期待", "盼望", "希望", "憧憬", "加油", "努力", "坚持", "进步", "成长",
    "收获", "成就", "突破", "无敌", "冠军", "太棒", "真棒", "好耶", "耶", "哇塞",
    /* 口语 / 网络用语 */
    "点赞", "好评", "绝了", "绝绝子", "牛批", "牛逼", "牛啊", "牛", "666", "溜",
    "秀", "秀儿", "上头", "爱了", "喜欢", "爱上", "心动", "好可爱", "好萌", "好磕",
    "磕到了", "甜到了", "笑死", "笑死我", "哈哈哈", "嘿嘿", "嘻嘻", "乐", "起飞", "芜湖",
    "好嘞", "好滴", "好的", "没问题", "稳", "稳了", "贴心", "暖心", "温柔", "善良",
    "友好", "和睦", "和谐", "团结", "互助", "陪伴", "支持", "鼓励", "理解", "包容",
    "尊重", "信任", "真诚", "坦诚", "开朗", "乐观", "积极", "向上", "阳光", "活力",
    "元气", "精神", "健康", "平安", "安稳", "顺遂", "圆满", "称心", "如意", "喜出望外",
    "喜洋洋", "乐呵呵", "美滋滋", "笑哈哈", "兴致勃勃", "精神抖擞", "神清气爽", "如释重负", "扬眉吐气", "春风得意",
    "锦上添花", "好事", "好运", "福星", "鸿运", "顺风顺水", "万事如意", "心想事成", "梦想成真", "旗开得胜",
    "马到成功", "大吉大利", "恭喜", "祝福", "庆贺", "庆祝", "开心极了", "高兴极了", "乐开花", "笑开花",
    "美美", "美美哒", "棒棒哒", "么么哒", "亲亲", "抱抱", "贴贴", "宠", "宠溺", "偏爱",
    "治愈系", "小确幸", "绝美", "无敌了", "封神", "天花板", "顶级", "高级", "惊艳", "哇",
    "哦耶", "耶斯", "欧耶", "耶耶", "棒极了", "太好了", "真好", "挺好", "蛮好", "还行",
    "可以", "行", "友好相处", "玩得开心", "吃好喝好", "睡得香", "睡得好", "香", "真香",
    "好棒", "好棒棒", "好厉害", "好强", "好牛", "好优秀", "好开心", "好幸福", "好喜欢", "好爱",
    "爱爱", "爱死", "喜欢死", "可爱死", "萌死", "甜死", "笑死了", "乐死", "美死了", "爽死",
    "舒坦了", "舒服了", "放松了", "安心了", "踏实了", "放心了", "解决了", "搞定了", "完成了", "达标了",
    "通过", "过关", "上岸", "成功上岸", "加薪", "升职", "录取", "考上了", "赢了", "赚到",
    "回本", "翻倍", "大赚", "稳赚", "值了", "值得", "不亏", "血赚", "赚翻", "暴富",
    "好运连连", "鸿运当头", "福气满满", "幸福满满", "元气满满", "能量满满", "状态好", "状态不错", "心情好", "心情不错",
    "开心心", "高兴兴", "快乐乐", "甜甜蜜蜜", "开开心心", "高高兴兴", "快快乐乐", "幸幸福福", "舒舒服服", "轻轻松松",
    "yyds", "永远的神", "tql", "太强了", "强无敌", "爱了爱了", "可爱多", "小可爱", "宝贝", "心肝",
    "满足感", "幸福感", "成就感", "安全感", "归属感", "被爱", "被宠", "被偏爱", "被理解", "被支持",
    "有人疼", "有人爱", "有人陪", "有人懂", "陪我", "陪我玩", "抱抱我", "亲亲我", "夸夸", "夸夸我",
    "表扬", "赞赏", "欣赏", "赞美", "崇拜", "羡慕", "佩服", "敬仰", "崇拜你", "爱慕",
    "惺惺相惜", "志同道合", "聊得来", "合得来", "默契", "心有灵犀", "懂我", "知音", "知己", "好朋友",
    "铁子", "老铁", "兄弟", "姐妹", "闺蜜", "基友", "搭子", "CP", "嗑", "嗑糖",
    "发糖", "甜蜜蜜", "粉红泡泡", "心动信号", "怦然心动", "一见钟情", "双向奔赴", "官宣", "领证", "结婚",
    "脱单", "恋爱", "甜甜的", "暖暖的", "软软的", "香香的", "漂漂亮亮", "可可爱爱", "奇奇怪怪", "古灵精怪"
  ];

  /* ==================== 二、消极词（≥300） ==================== */
  var NEG_WORDS = [
    /* 基础情绪 */
    "难过", "伤心", "悲伤", "悲痛", "痛苦", "疼", "痛", "心痛", "心碎", "哭",
    "哭泣", "流泪", "泪", "想哭", "委屈", "沮丧", "失落", "郁闷", "烦", "烦躁",
    "烦恼", "恼火", "生气", "愤怒", "火大", "怒", "暴怒", "抓狂", "崩溃", "绝望",
    "无助", "无奈", "无语", "心累", "累", "疲惫", "疲劳", "乏", "倦", "困",
    "乏味", "无聊", "没劲", "丧", "低落", "消沉", "抑郁", "压抑", "焦虑", "焦躁",
    "紧张", "不安", "慌", "慌张", "害怕", "恐惧", "恐怖", "吓人", "可怕", "担心",
    "担忧", "忧虑", "愁", "发愁", "苦恼", "纠结", "矛盾", "尴尬", "社死", "丢人",
    "丢脸", "羞愧", "内疚", "自责", "后悔", "遗憾", "失望", "扫兴", "倒霉", "衰",
    "晦气", "糟糕", "糟心", "恶心", "讨厌", "烦人", "恨", "怨恨", "嫉妒", "吃醋",
    "不爽", "不舒服", "不适", "难受", "煎熬", "折磨", "苦", "命苦", "惨", "悲催",
    /* 网络用语 / 口语 */
    "悲剧", "麻了", "麻木", "破防", "破大防", "emo", "玉玉", "自闭", "裂开", "绷不住",
    "蚌埠住了", "栓Q", "寄了", "完蛋", "完了", "凉了", "摆烂", "躺平", "佛系", "丧气",
    "心塞", "膈应", "泪目", "泪崩", "血压", "血压高", "离谱", "服了", "吐了", "淦",
    "靠", "操", "傻", "蠢", "笨", "垃圾", "废物", "废", "菜",
    "弱", "烂", "坑", "坑爹", "坑人", "骗子", "骗", "背叛", "渣",
    "渣男", "渣女", "分手", "失恋", "单身狗", "孤独", "寂寞", "空虚", "孤单", "思念",
    "舍不得", "难受死", "气死", "烦死", "累死", "困死", "饿", "饿死", "穷", "穷死",
    "缺钱", "没钱", "加班", "熬夜", "失眠", "睡不着", "噩梦", "心慌", "心悸", "胸闷",
    "头疼", "头痛", "肚子疼", "生病", "感冒", "发烧", "脱发", "秃头", "丑", "胖",
    "矮", "老土", "过时", "落伍", "淘汰", "输", "输了", "失败",
    "搞砸", "翻车", "出错", "错误", "麻烦", "困难", "压力", "压力大", "负担", "沉重",
    "累赘", "拖累", "拖延", "懒", "拖延症", "颓废", "堕落", "废柴", "没用", "无能",
    "无力", "emo了", "玉玉了", "抑郁了", "破防了", "绷不住了", "撑不住", "顶不住", "扛不住", "受不了",
    "受不了了", "难受极了", "痛不欲生", "心如刀割", "万念俱灰", "生无可恋", "没希望", "没意义", "没意思", "无趣",
    "枯燥", "灰心", "气馁", "挫败", "挫折", "打击", "受伤", "伤害", "欺负", "被欺负",
    "委屈巴巴", "可怜", "悲惨", "凄凉", "惨淡", "冷清", "想家", "哭死", "泪流满面", "泣不成声",
    "唉声叹气", "长叹", "叹气", "气死了", "累死了", "困死了", "饿死了", "社恐",
    "内耗", "精神内耗", "自我怀疑", "自卑", "不自信", "没自信", "讨好", "玻璃心", "敏感", "多想",
    "胡思乱想", "想太多", "钻牛角尖", "低谷", "低潮", "黑暗", "阴霾", "乌云", "雨天", "阴天",
    "寒冷", "冻", "难吃",
    "难喝", "难闻", "难看", "难听", "呕吐", "想吐", "头晕", "感冒了", "没精神", "没力气",
    "没胃口", "吃不下", "睡不着觉", "失眠了", "做噩梦", "心累死了", "身心俱疲", "精疲力尽", "疲惫不堪", "焦头烂额",
    "手忙脚乱", "一团糟", "乱七八糟", "烦乱", "心烦", "心烦意乱", "坐立不安", "如坐针毡", "惶惶不安", "提心吊胆",
    "胆战心惊", "胆怯", "退缩", "逃避", "不想面对", "不想上班", "不想上学", "不想起床", "不想说话", "不想理人",
    "想静静", "想躲起来", "透明人", "边缘人", "多余", "被讨厌", "被孤立", "被排挤", "排挤", "孤立",
    "冷落", "忽视", "忽略", "不在意", "随便", "敷衍", "冷漠", "冷淡", "疏远", "渐行渐远",
    "物是人非", "错过", "失去", "丢了", "损失", "亏", "亏了", "赔", "赔钱", "破财",
    "倒霉透顶", "诸事不顺", "水逆", "要疯了", "疯了", "mad", "angry", "sad", "cry", "tired",
    "depressed", "anxious", "lonely", "难受想哭", "想哭哭", "心里苦", "心里难受", "堵得慌", "憋屈", "憋闷"
  ];

  /* ==================== 三、关怀/风险词（强负面，命中后单独标记，绝不被平均掉） ==================== */
  var CARE_WORDS = [
    "想死", "不想活", "不想活了", "活着没意思", "活着没意义", "活不下去", "轻生", "自杀", "自尽", "了结",
    "想消失", "想解脱", "解脱", "撑不下去", "顶不住", "扛不住", "受不了了", "救救我", "救救我吧", "自残",
    "伤害自己", "割腕", "跳楼", "跳河", "安眠药", "一了百了", "生无可恋", "万念俱灰", "没希望了", "绝望透顶",
    "彻底绝望", "想结束", "结束自己", "结束生命", "离开这个世界", "想离开", "人间不值得", "不配活着", "我该死", "想不开",
    "抑郁到想死", "想死的心都有", "活着好累", "活着好难", "熬不下去", "熬不住", "彻底崩溃", "没有意义了", "没有活下去的勇气", "想放弃生命",
    "不想醒来", "永远睡下去", "消失掉", "从这个世界上消失", "消失", "撑不住", "熬不过去", "没脸见人", "恨自己", "惩罚自己",
    "自暴自弃", "破罐破摔", "烂命一条", "活着受罪", "生不如死", "不想面对明天", "看不到希望", "走不出来", "喘不过气", "心口疼"
  ];

  /* ==================== 四、修饰词 ==================== */
  var STRONG_ADV = ["很", "超", "太", "非常", "特别", "好", "巨", "贼", "超级", "极其",
    "十分", "相当", "格外", "尤其", "真", "真的", "死了", "死", "爆", "炸", "贼拉"];
  var WEAK_ADV = ["有点", "有些", "一点", "一点点", "稍微", "稍稍", "略", "略微", "还算", "勉强",
    "不太", "不怎么", "不太多", "一点点儿"];
  var NEGATORS = ["不", "没", "没有", "别", "无", "非", "未", "毫无", "不怎么", "不太",
    "不再", "从未", "绝不", "决不", "莫", "勿", "休想"];
  var NEGATION_WINDOW = 3;

  /* ==================== 五、emoji ==================== */
  var POS_EMOJI = ["😄", "😃", "😊", "😁", "😂", "🤣", "😆", "😍", "🥰", "😘", "😗", "😙", "😚",
    "🙂", "🙃", "😉", "🤩", "😎", "🥳", "😏", "😌", "😋", "🤗", "👍", "👏", "🙌", "🤝",
    "💪", "❤️", "🧡", "💛", "💚", "💙", "💜", "💖", "💗", "💓", "💞", "💕", "❣️", "🎉",
    "🎊", "✨", "🌟", "⭐", "🔥", "✅", "🆗", "🉑", "😺", "😸", "🙏", "😇", "🤙", "✌️"];
  var NEG_EMOJI = ["😭", "😢", "😡", "😠", "🤬", "😞", "😔", "😣", "😫", "😩", "💔", "😤",
    "😰", "😨", "😥", "😓", "🥶", "🤮", "🤧", "😷", "😵", "😖", "😦", "😧", "😪", "👎",
    "💀", "☠️", "🖕", "🙄", "😒", "😑", "😐", "😶", "😬", "😑"];

  /* ==================== 六、内部工具 ==================== */
  function escRe(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  function sortLongFirst(a, b) { return b.length - a.length; }

  var _regexCache = {};
  function reFor(words, key) {
    if (!_regexCache[key]) {
      _regexCache[key] = new RegExp(words.slice().sort(sortLongFirst).map(escRe).join("|"), "gi");
    }
    return _regexCache[key];
  }
  /* 在一段文本里找出所有词典命中（返回 {w, i}，正则交替顺序按词长降序，避免短词吃掉长词） */
  function findHits(text, words, key) {
    var out = [], re = reFor(words, key), m;
    re.lastIndex = 0;
    while ((m = re.exec(text)) !== null) {
      out.push({ w: m[0], i: m.index });
      if (m.index === re.lastIndex) re.lastIndex++;
    }
    return out;
  }
  function hasNegation(text, idx) {
    var before = text.slice(Math.max(0, idx - NEGATION_WINDOW), idx);
    for (var i = 0; i < NEGATORS.length; i++) {
      if (before.indexOf(NEGATORS[i]) !== -1) return true;
    }
    return false;
  }
  function degreeMult(text, idx, wordLen, word) {
    var before = text.slice(Math.max(0, idx - NEGATION_WINDOW), idx);
    var after = text.slice(idx + wordLen, idx + wordLen + NEGATION_WINDOW);
    var ctx = before + "|" + after;
    var m = 1;
    if (word && /(死了|死|爆|炸|极了|坏了)$/.test(word)) m *= 1.5;
    for (var i = 0; i < WEAK_ADV.length; i++) {
      if (ctx.indexOf(WEAK_ADV[i]) !== -1) { m *= 0.5; break; }
    }
    for (var j = 0; j < STRONG_ADV.length; j++) {
      if (ctx.indexOf(STRONG_ADV[j]) !== -1) { m *= 1.5; break; }
    }
    return m;
  }
  /* 连续字符组数：连续 >= min 次算 1 组 */
  function runGroups(text, ch, min) {
    var n = 0, i = 0;
    while (i < text.length) {
      if (text[i] === ch) {
        var j = i;
        while (j < text.length && text[j] === ch) j++;
        if (j - i >= min) n++;
        i = j;
      } else i++;
    }
    return n;
  }
  function runGroupsAny(text, chars, min) {
    var n = 0;
    for (var i = 0; i < chars.length; i++) n += runGroups(text, chars[i], min);
    return n;
  }
  function uniq(arr) {
    var seen = {}, out = [];
    for (var i = 0; i < arr.length; i++) { if (!seen[arr[i]]) { seen[arr[i]] = 1; out.push(arr[i]); } }
    return out;
  }
  function pad2(n) { return n < 10 ? "0" + n : "" + n; }
  function dayKey(ts) {
    var d = new Date(ts * 1000);
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }
  function round2(v) { return Math.round(v * 100) / 100; }
  function round3(v) { return Math.round(v * 1000) / 1000; }
  function field(m, a, b) { return m[a] != null ? m[a] : m[b]; }

  /* ==================== 七、单条消息打分 ====================
   * scoreMessage(text, opts) -> { pos, neg, care, score, senti, confidence,
   *                               pos_words, neg_words, care_words, night, signals }
   * opts 可为 number（小时 0-23）或 {hour} / {ts}（Unix 秒）。
   * score ∈ [-1,1]：正=积极，负=消极；confidence ∈ [0,1]：有信号才高。
   */
  function scoreMessage(text, opts) {
    var raw = (text == null) ? "" : String(text);
    var hour = null;
    if (typeof opts === "number") hour = opts;
    else if (opts && typeof opts === "object") {
      if (opts.hour != null) hour = Number(opts.hour);
      else if (opts.ts != null) hour = new Date(Number(opts.ts) * 1000).getHours();
    }
    var night = (hour != null) && (hour >= 23 || hour < 5);
    var res = {
      pos: 0, neg: 0, care: false, score: 0, senti: 0, confidence: 0,
      pos_words: [], neg_words: [], care_words: [], night: night, signals: []
    };
    if (!raw) return res;

    var t = raw.toLowerCase();
    var posW = 0, negW = 0;

    /* 词典命中：积极词 */
    findHits(t, POS_WORDS, "pos").forEach(function (h) {
      var w = 1.0 * degreeMult(t, h.i, h.w.length, h.w);
      if (hasNegation(t, h.i)) { negW += w; res.neg_words.push(h.w); }
      else { posW += w; res.pos_words.push(h.w); }
    });
    /* 词典命中：消极词 */
    findHits(t, NEG_WORDS, "neg").forEach(function (h) {
      var w = 1.0 * degreeMult(t, h.i, h.w.length, h.w);
      if (hasNegation(t, h.i)) { posW += w; res.pos_words.push(h.w); }
      else { negW += w; res.neg_words.push(h.w); }
    });
    /* 关怀/风险词 */
    findHits(t, CARE_WORDS, "care").forEach(function (h) {
      res.care = true;
      res.care_words.push(h.w);
    });
    /* emoji */
    var posEmoji = findHits(t, POS_EMOJI, "posemoji");
    var negEmoji = findHits(t, NEG_EMOJI, "negemoji");
    var posEmojiUniq = uniq(posEmoji.map(function (h) { return h.w; }));
    var negEmojiUniq = uniq(negEmoji.map(function (h) { return h.w; }));
    posW += Math.min(posEmoji.length, 3) * 0.8;
    negW += Math.min(negEmoji.length, 3) * 0.8;
    res.emoji_pos = posEmojiUniq.length; res.emoji_neg = negEmojiUniq.length;

    /* 行为信号：重复标点 / 重复字符 */
    var excl = runGroups(t, "！", 2) + runGroups(t, "!", 2);
    var ques = runGroups(t, "？", 2) + runGroups(t, "?", 2);
    var laugh = runGroupsAny(t, ["哈", "嘿", "嘻", "呵"], 2) + (/h{3,}/i.test(t) ? 1 : 0) + (/2{3,}/.test(t) ? 1 : 0);
    var cry = runGroupsAny(t, ["呜", "嘤"], 2) + runGroups(t, "啊", 3) + runGroupsAny(t, ["哎"], 2);
    posW += laugh * 0.6;
    negW += cry * 0.6;
    negW += ques * 0.15;

    /* 重复标点计强度：按当前极性方向加性加权 */
    if (excl) {
      var bonus = Math.min(1.0, 0.4 * excl);
      if (negW > posW) negW += bonus;
      else if (posW > negW) posW += bonus;
      else { posW += bonus * 0.5; negW += bonus * 0.5; }
    }

    /* 长度：极短消息（≤2 字）带情绪权重更高；超长消息稀释 */
    var L = raw.length;
    var lenMult = 1;
    if (posW + negW > 0) {
      if (L <= 2) lenMult = 1.25;
      else if (L >= 100) lenMult = 0.5;
      else if (L >= 50) lenMult = 0.75;
    }
    posW *= lenMult; negW *= lenMult;

    /* 深夜：负面情绪 ×1.3（自我观察核心信号） */
    if (night && negW > posW) negW *= 1.3;

    /* 关怀词：单独高亮，绝不参与正负对冲被平均掉 */
    if (res.care) { posW = 0; negW = Math.max(negW, 3.0); }

    var total = posW + negW;
    if (total > 0) {
      var ratio = (posW - negW) / total;      /* -1..1 */
      var sat = total / (total + 1);          /* 0..1 证据饱和度 */
      res.score = ratio * sat;
      res.confidence = Math.min(1, total / (total + 1.2)
        + (excl ? Math.min(0.3, 0.15 * excl) : 0)
        + (ques ? 0.1 : 0)
        + (night && res.score < 0 ? 0.05 : 0));
      if (res.care) res.confidence = Math.max(res.confidence, 0.9);
    }
    if (res.care) res.score = -Math.max(0.8, Math.abs(res.score));
    res.senti = res.score;
    res.pos = uniq(res.pos_words).length + posEmojiUniq.length + laugh;
    res.neg = uniq(res.neg_words).length + negEmojiUniq.length + cry + ques;
    if (excl) res.signals.push("excl:" + excl);
    if (ques) res.signals.push("ques:" + ques);
    if (laugh) res.signals.push("laugh:" + laugh);
    if (cry) res.signals.push("cry:" + cry);
    if (night) res.signals.push("night");
    if (res.care) res.signals.push("care");
    return res;
  }

  /* ==================== 八、主入口 computeReport ==================== */
  function computeReport(messages, meta) {
    messages = messages || [];
    meta = meta || {};
    var textOf = function (m) { return field(m, "x", "text") == null ? "" : String(field(m, "x", "text")); };
    var kindOf = function (m) { return field(m, "k", "kind") || "c2c"; };
    var peerOf = function (m) { var v = field(m, "p", "peer_id"); return v == null ? "unknown" : String(v); };
    var tsOf = function (m) { return Number(field(m, "t", "ts") || 0); };
    var dirOf = function (m) { return Number(field(m, "d", "direction") || 0); };

    var selfMsgs = messages.filter(function (m) { return m && dirOf(m) === 1; });
    var n = selfMsgs.length;

    var days = {}, hourHist = {}, peerMap = {};
    var posWordHits = {}, negWordHits = {}, careHits = {};
    var posSum = 0, negSum = 0, careMsgs = 0;
    var sentiW = 0, confW = 0, signalN = 0, nightN = 0, lenSum = 0, confSum = 0;
    var emojiPos = 0, emojiNeg = 0;
    var c2cSelf = 0, groupSelf = 0;
    var firstTs = null, lastTs = null;

    selfMsgs.forEach(function (m) {
      var x = textOf(m);
      var k = kindOf(m);
      var p = peerOf(m);
      var ts = tsOf(m);
      if (!ts) return;                      /* 脏数据：ts<=0 跳过 */
      var h = new Date(ts * 1000).getHours();
      var s = scoreMessage(x, h);

      if (firstTs === null || ts < firstTs) firstTs = ts;
      if (lastTs === null || ts > lastTs) lastTs = ts;
      if (k === "group") groupSelf++; else c2cSelf++;

      posSum += s.pos; negSum += s.neg;
      emojiPos += s.emoji_pos; emojiNeg += s.emoji_neg;
      if (s.care) careMsgs++;
      nightN += (h >= 23 || h < 5) ? 1 : 0;
      lenSum += x.length;
      hourHist[h] = (hourHist[h] || 0) + 1;

      confSum += s.confidence;
      if (s.confidence > 0) { signalN++; sentiW += s.score * s.confidence; confW += s.confidence; }

      /* 日聚合（按 confidence 加权） */
      var dk = dayKey(ts);
      var dd = days[dk] || (days[dk] = { count: 0, pos: 0, neg: 0, care: 0, wSum: 0, sentiW: 0, confSum: 0, signal: 0, lenSum: 0, night: 0 });
      dd.count++;
      dd.pos += s.pos; dd.neg += s.neg;
      if (s.care) dd.care++;
      dd.confSum += s.confidence;
      dd.lenSum += x.length;
      if (h >= 23 || h < 5) dd.night++;
      if (s.confidence > 0) { dd.signal++; dd.wSum += s.confidence; dd.sentiW += s.score * s.confidence; }

      /* 对象聚合 */
      var pk = k + "|" + p;
      var po = peerMap[pk] || (peerMap[pk] = { kind: k, qq: p, total: 0, self: 0, wSum: 0, sentiW: 0, signal: 0 });
      po.total++; po.self++;
      if (s.confidence > 0) { po.signal++; po.wSum += s.confidence; po.sentiW += s.score * s.confidence; }

      /* 高频情绪词（每条消息每个词只计一次，翻转后的词计入实际极性） */
      uniq(s.pos_words).forEach(function (w) { posWordHits[w] = (posWordHits[w] || 0) + 1; });
      uniq(s.neg_words).forEach(function (w) { negWordHits[w] = (negWordHits[w] || 0) + 1; });
      uniq(s.care_words).forEach(function (w) { careHits[w] = (careHits[w] || 0) + 1; });
    });

    /* 日明细 */
    var daily = Object.keys(days).sort().map(function (k) {
      var d = days[k];
      return {
        date: k,
        count: d.count,
        pos: d.pos, neg: d.neg, care: d.care,
        senti: d.wSum ? round3(d.sentiW / d.wSum) : 0,
        confidence: d.count ? round3(d.confSum / d.count) : 0,
        coverage: d.count ? round3(d.signal / d.count) : 0,
        lenAvg: d.count ? Math.round(d.lenSum / d.count) : 0,
        night: d.night
      };
    });

    /* 回复节奏 */
    var gaps = [];
    for (var i = 1; i < n; i++) {
      var g = tsOf(selfMsgs[i]) - tsOf(selfMsgs[i - 1]);
      if (g > 0 && g < 6 * 3600) gaps.push(g);
    }
    gaps.sort(function (a, b) { return a - b; });
    var medianGap = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 0;
    var avgGap = gaps.length ? Math.round(gaps.reduce(function (a, b) { return a + b; }, 0) / gaps.length) : 0;

    /* 对象分布 */
    var peers = Object.keys(peerMap).map(function (k) {
      var o = peerMap[k];
      return {
        kind: o.kind, qq: o.qq === "unknown" ? "未知对象" : o.qq,
        total: o.total, self: o.self,
        senti: o.wSum ? round3(o.sentiW / o.wSum) : 0,
        confidence: o.self ? round3(o.wSum / o.self) : 0,
        coverage: o.self ? round3(o.signal / o.self) : 0
      };
    }).sort(function (a, b) { return b.self - a.self; });

    var top = function (hitsMap, limit) {
      return Object.keys(hitsMap).sort(function (a, b) { return hitsMap[b] - hitsMap[a]; }).slice(0, limit)
        .map(function (w) { return { w: w, c: hitsMap[w] }; });
    };
    var nightHours = Object.keys(hourHist).filter(function (h) { return +h >= 23 || +h < 5; })
      .sort(function (a, b) { return hourHist[b] - hourHist[a]; }).map(function (h) { return +h; });

    var avgSenti = confW ? sentiW / confW : 0;
    var coverage = n ? signalN / n : 0;
    var avgConf = n ? confSum / n : 0;

    var timeStart = meta.time_start != null ? meta.time_start : (meta.first_ts != null ? meta.first_ts : firstTs);
    var timeEnd = meta.time_end != null ? meta.time_end : (meta.last_ts != null ? meta.last_ts : lastTs);
    var totalMessages = meta.total_messages != null ? meta.total_messages : (meta.messages != null ? meta.messages : messages.length);

    return {
      generated: meta.generated,
      self_qq: meta.self_qq != null ? meta.self_qq : (meta.account_qq != null ? meta.account_qq : (meta.qq != null ? meta.qq : null)),
      time_start: timeStart,
      time_end: timeEnd,
      overview: {
        total_messages: totalMessages,
        self_messages: n,
        c2c_self: meta.c2c_self != null ? meta.c2c_self : c2cSelf,
        group_self: meta.group_self != null ? meta.group_self : groupSelf,
        active_days: daily.length,
        avg_per_day: daily.length ? Math.round(n / daily.length * 10) / 10 : 0,
        avg_len: n ? Math.round(lenSum / n) : 0,
        night_count: nightN,
        night_ratio: n ? Math.round(nightN / n * 100) : 0,
        median_gap_min: Math.round(medianGap / 60),
        avg_gap_min: Math.round(avgGap / 60),
        signal_messages: signalN,
        coverage: round3(coverage),
        avg_confidence: round3(avgConf),
        lexicon_version: LEXICON_VERSION
      },
      hour_hist: Object.keys(hourHist).map(function (h) { return [+h, hourHist[h]]; })
        .sort(function (a, b) { return a[0] - b[0]; }),
      sentiment: {
        pos_hits: posSum, neg_hits: negSum,
        pos_ratio: (posSum + negSum) ? Math.round(posSum / (posSum + negSum) * 100) : 0,
        neg_ratio: (posSum + negSum) ? Math.round(negSum / (posSum + negSum) * 100) : 0,
        avg_senti: round2(avgSenti),
        care_msgs: careMsgs,
        emoji_pos: emojiPos, emoji_neg: emojiNeg,
        coverage: round3(coverage),
        signal_messages: signalN,
        avg_confidence: round3(avgConf),
        lexicon_version: LEXICON_VERSION
      },
      night_hours: nightHours.slice(0, 3),
      daily: daily,
      peers: peers.slice(0, 10),
      top_pos_words: top(posWordHits, 8),
      top_neg_words: top(negWordHits, 8),
      care_words: careHits
    };
  }

  /* ==================== 九、导出 ==================== */
  var api = {
    computeReport: computeReport,
    scoreMessage: scoreMessage,
    lexiconInfo: function () {
      return {
        version: LEXICON_VERSION,
        pos_words: POS_WORDS.length,
        neg_words: NEG_WORDS.length,
        care_words: CARE_WORDS.length,
        pos_emoji: POS_EMOJI.length,
        neg_emoji: NEG_EMOJI.length,
        strong_adv: STRONG_ADV.length,
        weak_adv: WEAK_ADV.length,
        negators: NEGATORS.length
      };
    }
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.QQScopeEngine = api;
})(typeof window !== "undefined" ? window : globalThis);