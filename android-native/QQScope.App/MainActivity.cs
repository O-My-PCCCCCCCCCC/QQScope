using System.Text.Json;
using Android.App;
using Android.Content;
using Android.Graphics;
using Android.OS;
using Android.Runtime;
using Android.Util;
using Android.Views;
using Android.Webkit;
using Android.Widget;
using Java.Interop;

namespace QQScope.App;

[Activity(Label = "QQScope", MainLauncher = true, LaunchMode = Android.Content.PM.LaunchMode.SingleTop,
    ConfigurationChanges = Android.Content.PM.ConfigChanges.Orientation | Android.Content.PM.ConfigChanges.ScreenSize)]
public class MainActivity : Activity
{
    QqBot? _bot;
    WebView? _web;
    ImageView? _qrImage;
    TextView? _qrStatus;
    LinearLayout? _loginPanel;
    Button? _qrRefresh;

    protected override void OnCreate(Bundle? savedInstanceState)
    {
        base.OnCreate(savedInstanceState);
        RequestWindowFeature(WindowFeatures.NoTitle);
        Window.SetStatusBarColor(Color.ParseColor("#111827"));
        Window.SetNavigationBarColor(Color.ParseColor("#111827"));

        // 根布局
        var root = new LinearLayout(this) { Orientation = Orientation.Vertical };
        SetContentView(root);

        // 登录面板
        _loginPanel = new LinearLayout(this)
        {
            Orientation = Orientation.Vertical
        };
        _loginPanel.SetPadding(48, 120, 48, 48);
        var title = new TextView(this) { Text = "QQSCOPE · 扫码登录" };
        title.SetTextColor(Color.ParseColor("#111827"));
        title.TextSize = 22;
        title.Gravity = GravityFlags.Center;
        _loginPanel.AddView(title, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MatchParent, ViewGroup.LayoutParams.WrapContent));

        _qrImage = new ImageView(this) { LayoutParameters = new LinearLayout.LayoutParams(300, 300) { Gravity = GravityFlags.Center } };
        _loginPanel.AddView(_qrImage);

        _qrStatus = new TextView(this) { Text = "正在获取二维码..." };
        _qrStatus.SetTextColor(Color.ParseColor("#6b7280"));
        _qrStatus.TextSize = 14;
        _qrStatus.Gravity = GravityFlags.Center;
        _qrStatus.SetPadding(0, 20, 0, 0);
        _loginPanel.AddView(_qrStatus, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MatchParent, ViewGroup.LayoutParams.WrapContent));

        _qrRefresh = new Button(this) { Text = "刷新二维码" };
        _loginPanel.AddView(_qrRefresh, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WrapContent, ViewGroup.LayoutParams.WrapContent) { Gravity = GravityFlags.Center, TopMargin = 24 });
        _qrRefresh.Click += (_, _) => _ = RestartLoginAsync();

        // 账号密码登录（备选）
        var pwToggle = new TextView(this) { Text = "或使用账号密码登录 ▼", Gravity = GravityFlags.Center };
        pwToggle.SetTextColor(Color.ParseColor("#6366f1"));
        pwToggle.TextSize = 13;
        pwToggle.SetPadding(0, 20, 0, 0);
        _loginPanel.AddView(pwToggle, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MatchParent, ViewGroup.LayoutParams.WrapContent));

        var pwPanel = new LinearLayout(this) { Orientation = Orientation.Vertical, Visibility = ViewStates.Gone };
        pwPanel.SetPadding(0, 16, 0, 0);
        var uinInput = new EditText(this) { Hint = "QQ 号", InputType = Android.Text.InputTypes.ClassNumber };
        uinInput.SetTextColor(Color.ParseColor("#111827"));
        pwPanel.AddView(uinInput, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MatchParent, ViewGroup.LayoutParams.WrapContent) { BottomMargin = 10 });
        var pwInput = new EditText(this) { Hint = "密码", InputType = Android.Text.InputTypes.TextVariationPassword };
        pwInput.SetTextColor(Color.ParseColor("#111827"));
        pwPanel.AddView(pwInput, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MatchParent, ViewGroup.LayoutParams.WrapContent));
        var pwLogin = new Button(this) { Text = "登录" };
        pwPanel.AddView(pwLogin, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WrapContent, ViewGroup.LayoutParams.WrapContent) { Gravity = GravityFlags.Center, TopMargin = 14 });
        pwToggle.Click += (_, _) => pwPanel.Visibility = pwPanel.Visibility == ViewStates.Gone ? ViewStates.Visible : ViewStates.Gone;
        pwLogin.Click += async (_, _) =>
        {
            if (_bot == null || !long.TryParse(uinInput.Text, out var uin)) { _qrStatus!.Text = "请输入正确的 QQ 号"; return; }
            var pw = pwInput.Text ?? "";
            if (pw.Length == 0) { _qrStatus!.Text = "请输入密码"; return; }
            pwLogin.Enabled = false;
            _qrStatus!.Text = "正在登录...";
            try { await _bot.LoginByPasswordAsync(uin, pw); }
            catch (Exception ex) { _qrStatus.Text = "登录异常: " + ex.Message; }
            pwLogin.Enabled = true;
        };
        _loginPanel.AddView(pwPanel, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MatchParent, ViewGroup.LayoutParams.WrapContent));

        root.AddView(_loginPanel, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MatchParent, ViewGroup.LayoutParams.MatchParent));

        // WebView 仪表盘（登录后显示）
        _web = new WebView(this);
        _web.Settings.JavaScriptEnabled = true;
        _web.Settings.DomStorageEnabled = true;
        _web.Settings.AllowFileAccess = true;
        _web.Settings.AllowContentAccess = true;
        _web.Settings.LoadWithOverviewMode = true;
        _web.Settings.UseWideViewPort = true;
        _web.Settings.TextZoom = 100;
        _web.SetBackgroundColor(Color.White);
        root.AddView(_web, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MatchParent, ViewGroup.LayoutParams.MatchParent));

        // 启动协议核心
        var ks = System.IO.Path.Combine(FilesDir.AbsolutePath, "qqscope_keystore.json");
        _bot = new QqBot(ks);
        _bot.QrImage += bytes => RunOnUiThread(() =>
        {
            try
            {
                var bmp = BitmapFactory.DecodeByteArray(bytes, 0, bytes.Length);
                _qrImage?.SetImageBitmap(bmp);
                if (_qrStatus != null) _qrStatus.Text = "请用手机 QQ 扫码";
            }
            catch { }
        });
        _bot.QrUrl += url => Log.Info("QQScope", $"[QR] {url}");
        _bot.LoginResult += (ok, msg) => RunOnUiThread(() =>
        {
            _qrStatus!.Text = msg;
            if (ok) ShowDashboard();
        });
        _bot.Log += s => Log.Info("QQScope", s);
        _bot.MessageJson += json => RunOnUiThread(() =>
            _web?.EvaluateJavascript($"window.__nativeMsg && window.__nativeMsg({json})", null));

        _ = _bot.StartWithErrorSurfacingAsync();
    }

    async Task RestartLoginAsync()
    {
        _qrStatus!.Text = "正在获取二维码...";
        await _bot!.LogoutAsync();
        await _bot.StartWithErrorSurfacingAsync();
    }

    void ShowDashboard()
    {
        if (_web == null || _bot == null) return;
        _loginPanel!.Visibility = ViewStates.Gone;
        _web.Visibility = ViewStates.Visible;
        _web.AddJavascriptInterface(new NativeBridge(_bot, this), "QqScopeNative");
        _web.SetWebViewClient(new WebViewClient());
        _web.LoadUrl("file:///android_asset/QQScope.html");
    }

    public void PushJs(string script) => RunOnUiThread(() => _web?.EvaluateJavascript(script, null));

    protected override void OnDestroy()
    {
        _bot?.LogoutAsync().Wait(1000);
        base.OnDestroy();
    }
}
