using System.Text.Json;
using Android.Webkit;

namespace QQScope.App;

/// <summary>WebView JS 桥：window.QqScopeNative.* 返回 JSON 字符串</summary>
public class NativeBridge : Java.Lang.Object
{
    readonly QqBot _bot;
    readonly MainActivity _activity;
    bool _pulling;

    public NativeBridge(QqBot bot, MainActivity activity)
    {
        _bot = bot;
        _activity = activity;
    }

    [JavascriptInterface]
    public string GetState()
    {
        return JsonSerializer.Serialize(new
        {
            loggedIn = _bot.IsOnline,
            uin = _bot.Uin,
            nickname = _bot.Nickname
        });
    }

    /// <summary>返回当前缓存数据（不阻塞）</summary>
    [JavascriptInterface]
    public string GetData()
    {
        return _bot.DataJson();
    }

    /// <summary>异步拉取历史（好友列表 + c2c 漫游），JS 侧轮询 GetData</summary>
    [JavascriptInterface]
    public string RefreshData()
    {
        if (_pulling) return "started";
        _pulling = true;
        _ = Task.Run(async () =>
        {
            try
            {
                await _bot.PullHistoryAsync();
                _activity.PushJs("window.__dataReady && window.__dataReady()");
            }
            catch (Exception e)
            {
                _activity.PushJs($"window.__dataError && window.__dataError({JsonSerializer.Serialize(e.Message)})");
            }
            finally
            {
                _pulling = false;
            }
        });
        return "started";
    }

    /// <summary>按 QQ 号查陌生人资料（昵称/签名）</summary>
    [JavascriptInterface]
    public string GetStranger(string uin)
    {
        if (long.TryParse(uin, out var qq)) return _bot.StrangerJsonAsync(qq).GetAwaiter().GetResult();
        return "{}";
    }
}
