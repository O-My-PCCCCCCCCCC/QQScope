using System.Text.Json;
using Lagrange.Core;
using Lagrange.Core.Common;
using Lagrange.Core.Common.Entity;
using Lagrange.Core.Common.Interface;
using Lagrange.Core.Events.EventArgs;
using Lagrange.Core.Message;
using Lagrange.Core.Message.Entities;

namespace QQScope.App;

/// <summary>协议核心封装：登录（二维码/keystore 免重登）、实时消息、历史拉取、资料查询</summary>
public class QqBot
{
    BotContext? _ctx;
    readonly string _keystorePath;

    // 事件
    public event Action<byte[]>? QrImage;
    public event Action<string>? QrUrl;
    public event Action<bool, string>? LoginResult;      // 成功, 信息
    public event Action<string>? Log;                    // 协议日志
    public event Action<string>? MessageJson;            // 实时消息（JSON 行）

    // 缓存
    List<Dictionary<string, object>> _messages = new();
    Dictionary<string, object>? _meta;

    public bool IsOnline => _ctx?.IsOnline ?? false;
    public long Uin => _ctx?.BotUin ?? 0;
    public string Nickname => _ctx?.BotInfo?.Name ?? "";

    public QqBot(string keystorePath) => _keystorePath = keystorePath;

    /// <summary>自定义 AppInfo：Linux 协议 + 声称版本（QQ Linux 当前稳定版 3.2.32，Flathub 确认）</summary>
    static BotAppInfo MakeAppInfo() => new()
    {
        Os = "Linux",
        Kernel = "Linux",
        VendorOs = "linux",
        Qua = "V1_LNX_NQ_3.2.32_49672_GW_B",
        CurrentVersion = "3.2.32-49672",
        PtVersion = "2.0.0",
        SsoVersion = 19,
        PackageName = "com.tencent.qq",
        ApkSignatureMd5 = "com.tencent.qq"u8.ToArray(),
        SdkInfo = new WtLoginSdkInfo
        {
            SdkBuildTime = 0,
            SdkVersion = "nt.wtlogin.0.0.1",
            MiscBitMap = 32764,
            SubSigMap = 0,
            MainSigMap = (Sig)169742560
        },
        AppId = 1600001615,
        SubAppId = 537345891,
        AppClientVersion = 49672
    };

    public async Task StartAsync()
    {
        BotKeystore? keystore = null;
        if (File.Exists(_keystorePath))
        {
            try { keystore = JsonSerializer.Deserialize<BotKeystore>(await File.ReadAllTextAsync(_keystorePath)); }
            catch { }
        }

        var config = new BotConfig { Protocol = Protocols.Linux, LogLevel = LogLevel.Information };
        var appInfo = MakeAppInfo();
        _ctx = keystore == null
            ? BotFactory.Create(config, appInfo)
            : BotFactory.Create(config, keystore, appInfo);

        _ctx.EventInvoker.RegisterEvent<BotLogEvent>((_, e) => Log?.Invoke(e.ToString() ?? ""));
        _ctx.EventInvoker.RegisterEvent<BotQrCodeEvent>((_, e) =>
        {
            if (e.Image is { Length: > 0 }) QrImage?.Invoke(e.Image);
            QrUrl?.Invoke(e.Url);
        });
        _ctx.EventInvoker.RegisterEvent<BotLoginEvent>((_, e) =>
            LoginResult?.Invoke(e.Success, e.Error?.Message ?? (e.Success ? "登录成功" : "登录失败")));
        _ctx.EventInvoker.RegisterEvent<BotRefreshKeystoreEvent>((_, e) =>
        {
            try { Task.Run(() => File.WriteAllTextAsync(_keystorePath, JsonSerializer.Serialize(e.Keystore))); } catch { }
        });
        _ctx.EventInvoker.RegisterEvent<BotNewDeviceVerifyEvent>((_, e) =>
            Log?.Invoke($"[新设备验证] {e}"));
        _ctx.EventInvoker.RegisterEvent<BotCaptchaEvent>((_, e) =>
            Log?.Invoke($"[验证码] {e}"));

        _ctx.EventInvoker.RegisterEvent<BotMessageEvent>((_, e) =>
        {
            var row = ToMsg(e.Message);
            if (row != null)
            {
                lock (_messages)
                {
                    _messages.Add(row);
                    if (_meta != null)
                    {
                        _meta["total_messages"] = (long)_meta["total_messages"]! + 1;
                        if ((long)row["d"] == 1) _meta["self_messages"] = (long)_meta["self_messages"]! + 1;
                        if ((long)row["t"] < (long)_meta["time_start"]!) _meta["time_start"] = row["t"];
                        if ((long)row["t"] > (long)_meta["time_end"]!) _meta["time_end"] = row["t"];
                    }
                }
                MessageJson?.Invoke(JsonSerializer.Serialize(row));
            }
        });

        await _ctx.Login();
        Log?.Invoke(IsOnline ? "已上线" : "等待登录...");
    }

    /// <summary>带异常暴露的启动（fire-and-forget 时也能看到错误）</summary>
    public async Task StartWithErrorSurfacingAsync()
    {
        try
        {
            await StartAsync();
        }
        catch (Exception ex)
        {
            Log?.Invoke($"[启动异常] {ex.Message}");
            Log?.Invoke($"[堆栈] {ex.StackTrace}");
            LoginResult?.Invoke(false, "启动异常: " + ex.Message);
        }
    }

    public Task LogoutAsync() => _ctx?.Logout() ?? Task.CompletedTask;

    /// <summary>账号密码登录（替代/补充扫码）</summary>
    public async Task<bool> LoginByPasswordAsync(long uin, string password)
    {
        if (_ctx == null) return false;
        return await _ctx.Login(uin, password);
    }

    static string MsgText(BotMessage m)
    {
        var parts = m.Entities.OfType<TextEntity>().Select(t => t.Text);
        return string.Concat(parts).Trim();
    }

    /// <summary>转为 App 消息行 {t,d,p,k,x}；null 表示无文本</summary>
    Dictionary<string, object>? ToMsg(BotMessage m)
    {
        var text = MsgText(m);
        if (text.Length == 0) return null;
        bool isGroup = m.Type == MessageType.Group;
        long self = _ctx!.BotUin;
        long peer = 0;
        int dir;
        if (isGroup)
        {
            peer = m.Contact is BotGroupMember gm ? gm.Group.GroupUin : m.Contact.Uin;
            dir = m.Contact.Uin == self ? 1 : 0;
        }
        else
        {
            bool fromSelf = m.Contact.Uin == self;
            peer = fromSelf ? m.Receiver.Uin : m.Contact.Uin;
            dir = fromSelf ? 1 : 0;
        }
        return new Dictionary<string, object>
        {
            ["t"] = m.Time,
            ["d"] = dir,
            ["p"] = peer,
            ["k"] = isGroup ? "group" : "c2c",
            ["x"] = text
        };
    }

    /// <summary>拉取历史：好友列表 + 最近会话的漫游消息（c2c），并生成 meta</summary>
    public async Task PullHistoryAsync(int topPeers = 20, int perPeer = 60)
    {
        if (_ctx == null || !_ctx.IsOnline) return;
        var friends = await _ctx.FetchFriends();
        var groups = await _ctx.FetchGroups();

        var now = (uint)DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var newRows = new List<Dictionary<string, object>>();
        long minT = long.MaxValue, maxT = 0, total = 0, selfN = 0;

        // c2c 漫游历史
        var tasks = friends.Take(topPeers).Select(async f =>
        {
            try
            {
                var msgs = await _ctx.GetRoamMessage(f.Uin, now, (uint)perPeer);
                return msgs.Select(ToMsg).Where(x => x != null).ToList()!;
            }
            catch { return new List<Dictionary<string, object>>(); }
        }).ToArray();
        var results = await Task.WhenAll(tasks);
        foreach (var list in results) newRows.AddRange(list);

        lock (_messages)
        {
            // 去重（按 t+d+p+x）
            var seen = new HashSet<string>(_messages.Select(r => Key(r)));
            foreach (var r in newRows) if (seen.Add(Key(r))) _messages.Add(r);
            _messages.Sort((a, b) => ((long)a["t"]).CompareTo((long)b["t"]));
            total = _messages.Count;
            foreach (var r in _messages)
            {
                if ((long)r["t"] < minT) minT = (long)r["t"];
                if ((long)r["t"] > maxT) maxT = (long)r["t"];
                if ((long)r["d"] == 1) selfN++;
            }
            _meta = new Dictionary<string, object>
            {
                ["qq"] = _ctx.BotUin,
                ["label"] = Nickname,
                ["total_messages"] = total,
                ["self_messages"] = selfN,
                ["c2c_total"] = _messages.Count(r => (string)r["k"] == "c2c"),
                ["c2c_self"] = _messages.Count(r => (string)r["k"] == "c2c" && (long)r["d"] == 1),
                ["group_total"] = _messages.Count(r => (string)r["k"] == "group"),
                ["group_self"] = _messages.Count(r => (string)r["k"] == "group" && (long)r["d"] == 1),
                ["time_start"] = minT == long.MaxValue ? 0 : minT,
                ["time_end"] = maxT,
                ["friend_count"] = friends.Count,
                ["group_count"] = groups.Count,
                ["friends"] = friends.Take(30).Select(f => new { qq = f.Uin, name = f.Nickname }).ToList(),
                ["groups"] = groups.Take(30).Select(g => new { qq = g.GroupUin, name = g.GroupName }).ToList()
            };
        }
    }

    static string Key(Dictionary<string, object> r) =>
        $"{r["t"]}|{r["d"]}|{r["p"]}|{r["k"]}|{r["x"]}";

    public string DataJson()
    {
        lock (_messages)
        {
            var payload = new { messages = _messages, meta = _meta ?? new Dictionary<string, object>() };
            return JsonSerializer.Serialize(payload);
        }
    }

    /// <summary>陌生人资料（昵称/签名）—— 解决"社交对象只有QQ号"</summary>
    public async Task<string> StrangerJsonAsync(long uin)
    {
        try
        {
            if (_ctx == null || !_ctx.IsOnline) return "{}";
            var s = await _ctx.FetchStranger(uin);
            return JsonSerializer.Serialize(new { uin = s.Uin, nickname = s.Nickname, sign = s.PersonalSign });
        }
        catch { return "{}"; }
    }
}
