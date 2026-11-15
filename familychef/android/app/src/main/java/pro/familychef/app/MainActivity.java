package pro.familychef.app;

import android.annotation.SuppressLint;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ImageView;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;

/**
 * FamilyChef: открывает сайт во встроенном WebView.
 * Не зависит от установленного браузера и настроек оболочки телефона.
 */
public class MainActivity extends ComponentActivity {

    static final String HOST = "familychef.pro";
    static final String START_URL = "https://" + HOST + "/?source=android";
    static final String OFFLINE_HTML =
        "<!doctype html><html lang=ru><meta name=viewport content='width=device-width,initial-scale=1'>"
        + "<body style='margin:0;font-family:sans-serif;background:#f6f5f1;color:#1f2421;display:flex;"
        + "align-items:center;justify-content:center;height:100vh;text-align:center'><div style='padding:24px'>"
        + "<h2>Нет подключения к интернету</h2><p>Проверьте Wi-Fi или мобильный интернет и попробуйте снова.</p>"
        + "<button onclick='FamilyChefAndroid.retry()' style='font-size:17px;padding:12px 28px;border:0;"
        + "border-radius:8px;background:#2f7d4f;color:#fff'>Повторить</button></div></body></html>";

    WebView webView;
    private View splash;
    private boolean showingOffline;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF2F7D4F);

        webView = new WebView(this);
        webView.setBackgroundColor(0xFFF6F5F1);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);      // localStorage: настройки, свои цены, отметки «куплено»
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setUserAgentString(s.getUserAgentString() + " FamilyChefApp/" + BuildConfig.VERSION_NAME);
        webView.addJavascriptInterface(new Bridge(), "FamilyChefAndroid");
        webView.setWebViewClient(new Client());
        root.addView(webView, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        ImageView logo = new ImageView(this);
        logo.setImageResource(R.mipmap.ic_launcher_foreground);
        logo.setScaleType(ImageView.ScaleType.CENTER_INSIDE);
        logo.setBackgroundColor(0xFF2F7D4F);
        splash = logo;
        root.addView(splash, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // Android 15+ рисует приложение под системными панелями — отступаем от них.
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            int top, bottom;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(
                    WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                top = bars.top; bottom = bars.bottom;
            } else {
                top = insets.getSystemWindowInsetTop(); bottom = insets.getSystemWindowInsetBottom();
            }
            FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) webView.getLayoutParams();
            lp.topMargin = top; lp.bottomMargin = bottom;
            webView.setLayoutParams(lp);
            return insets;
        });

        setContentView(root);

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override public void handleOnBackPressed() {
                if (!showingOffline && webView.canGoBack()) {
                    webView.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });

        // Заставка не дольше 4 секунд, даже если сеть медленная.
        new Handler(Looper.getMainLooper()).postDelayed(this::hideSplash, 4000);

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            Uri data = getIntent() != null ? getIntent().getData() : null;
            webView.loadUrl(data != null && HOST.equals(data.getHost()) ? data.toString() : START_URL);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        Uri data = intent.getData();
        if (data != null && HOST.equals(data.getHost())) webView.loadUrl(data.toString());
    }

    @Override
    protected void onDestroy() {
        if (webView != null) webView.destroy();
        super.onDestroy();
    }

    void hideSplash() {
        if (splash != null && splash.getVisibility() == View.VISIBLE) {
            splash.animate().alpha(0f).setDuration(250).withEndAction(() -> splash.setVisibility(View.GONE));
        }
    }

    static boolean isOwnSite(Uri uri) {
        return uri != null && "https".equals(uri.getScheme()) && HOST.equals(uri.getHost());
    }

    void openExternal(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (ActivityNotFoundException ignored) { }
    }

    private class Client extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            if (isOwnSite(uri)) return false;   // свой сайт — внутри приложения
            openExternal(uri);                  // остальные ссылки — в браузере
            return true;
        }

        @Override
        public void onPageStarted(WebView view, String url, Bitmap favicon) {
            if (url != null && url.startsWith("https://")) showingOffline = false;
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            hideSplash();
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) {
                showingOffline = true;
                view.loadDataWithBaseURL("https://" + HOST + "/offline", OFFLINE_HTML, "text/html", "utf-8", null);
                hideSplash();
            }
        }
    }

    /** Методы, доступные странице как window.FamilyChefAndroid. */
    private class Bridge {
        @JavascriptInterface
        public void share(String text) {
            Intent send = new Intent(Intent.ACTION_SEND);
            send.setType("text/plain");
            send.putExtra(Intent.EXTRA_TEXT, text);
            runOnUiThread(() -> {
                try { startActivity(Intent.createChooser(send, "Список покупок")); }
                catch (ActivityNotFoundException ignored) { }
            });
        }

        @JavascriptInterface
        public void retry() {
            runOnUiThread(() -> { showingOffline = false; webView.loadUrl(START_URL); });
        }
    }
}
