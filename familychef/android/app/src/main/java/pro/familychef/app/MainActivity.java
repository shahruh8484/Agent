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
import android.content.res.Configuration;
import android.view.Gravity;
import android.view.WindowInsetsController;
import android.widget.FrameLayout;
import android.widget.ImageView;

import android.webkit.CookieManager;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import androidx.core.content.ContextCompat;
import androidx.credentials.Credential;
import androidx.credentials.CredentialManager;
import androidx.credentials.CredentialManagerCallback;
import androidx.credentials.CustomCredential;
import androidx.credentials.GetCredentialRequest;
import androidx.credentials.GetCredentialResponse;
import androidx.credentials.exceptions.GetCredentialCancellationException;
import androidx.credentials.exceptions.GetCredentialException;

import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption;
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential;

import org.json.JSONObject;

import java.util.Locale;

/**
 * FamilyChef: открывает сайт во встроенном WebView.
 * Не зависит от установленного браузера и настроек оболочки телефона.
 */
public class MainActivity extends ComponentActivity {

    static final String HOST = "familychef.pro";
    static final String START_URL = "https://" + HOST + "/?source=android";
    static String offlineHtml() {
        boolean ru = java.util.Arrays.asList("ru", "uk", "be", "kk", "uz", "ky", "tg", "az", "hy", "ka", "tk")
            .contains(Locale.getDefault().getLanguage());
        String title = ru ? "Нет подключения к интернету" : "No internet connection";
        String text = ru ? "Проверьте Wi-Fi или мобильный интернет и попробуйте снова."
                         : "Check your Wi-Fi or mobile data and try again.";
        String button = ru ? "Повторить" : "Try again";
        return "<!doctype html><html><meta name=viewport content='width=device-width,initial-scale=1'>"
            + "<body style='margin:0;font-family:sans-serif;background:#f7f3ec;color:#1d2420;display:flex;"
            + "align-items:center;justify-content:center;height:100vh;text-align:center'><div style='padding:24px'>"
            + "<h2>" + title + "</h2><p>" + text + "</p>"
            + "<button onclick='FamilyChefAndroid.retry()' style='font-size:17px;padding:12px 28px;border:0;"
            + "border-radius:999px;background:#1f6b43;color:#fff'>" + button + "</button></div></body></html>";
    }

    WebView webView;
    private View splash;
    private boolean showingOffline;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF1F6B43);

        boolean night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
            == Configuration.UI_MODE_NIGHT_YES;
        webView = new WebView(this);
        webView.setBackgroundColor(night ? 0xFF121614 : 0xFFF7F3EC);
        webView.setOverScrollMode(View.OVER_SCROLL_NEVER);  // no web-page "bounce" at the edges
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

        // The strip under the bottom tab bar (gesture area) takes the tab bar colour, not the brand green.
        View navStrip = new View(this);
        navStrip.setBackgroundColor(night ? 0xFF1C2320 : 0xFFFFFFFF);
        FrameLayout.LayoutParams stripLp = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0);
        stripLp.gravity = Gravity.BOTTOM;
        root.addView(navStrip, stripLp);

        ImageView logo = new ImageView(this);
        logo.setImageResource(R.mipmap.ic_launcher_foreground);
        logo.setScaleType(ImageView.ScaleType.CENTER_INSIDE);
        logo.setBackgroundColor(0xFF1F6B43);
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
            FrameLayout.LayoutParams sl = (FrameLayout.LayoutParams) navStrip.getLayoutParams();
            sl.height = bottom;
            navStrip.setLayoutParams(sl);
            return insets;
        });

        setContentView(root);
        if (Build.VERSION.SDK_INT >= 30 && !night) {
            // dark navigation-bar icons on the light strip; status bar stays light-on-green
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) c.setSystemBarsAppearance(
                WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
                WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
        }

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
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();  // keep the sign-in session after the app is closed
    }

    @Override
    protected void onDestroy() {
        if (webView != null) webView.destroy();
        super.onDestroy();
    }

    /** Native Google sign-in (Google does not allow its sign-in page inside a WebView). */
    void startGoogleSignIn(String clientId) {
        if (clientId == null || !clientId.endsWith(".apps.googleusercontent.com")) {
            sendToPage("fcAndroidGoogleError", "failed");
            return;
        }
        GetSignInWithGoogleOption option = new GetSignInWithGoogleOption.Builder(clientId).build();
        GetCredentialRequest request = new GetCredentialRequest.Builder().addCredentialOption(option).build();
        CredentialManager.create(this).getCredentialAsync(this, request, null,
            ContextCompat.getMainExecutor(this),
            new CredentialManagerCallback<GetCredentialResponse, GetCredentialException>() {
                @Override public void onResult(GetCredentialResponse response) {
                    Credential c = response.getCredential();
                    if (c instanceof CustomCredential
                            && GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL.equals(c.getType())) {
                        try {
                            sendToPage("fcAndroidGoogle", GoogleIdTokenCredential.createFrom(c.getData()).getIdToken());
                            return;
                        } catch (Exception ignored) { }
                    }
                    sendToPage("fcAndroidGoogleError", "failed|unexpected credential type");
                }
                @Override public void onError(GetCredentialException e) {
                    if (e instanceof GetCredentialCancellationException) {
                        sendToPage("fcAndroidGoogleError", "cancelled");
                        return;
                    }
                    // Short reason for the screen, e.g. "NoCredentialException: no Google account".
                    String why = e.getClass().getSimpleName()
                        + (e.getMessage() != null ? ": " + e.getMessage() : "");
                    sendToPage("fcAndroidGoogleError", "failed|" + (why.length() > 160 ? why.substring(0, 160) : why));
                }
            });
    }

    void sendToPage(String function, String arg) {
        webView.evaluateJavascript("window." + function + " && window." + function + "("
            + JSONObject.quote(arg) + ")", null);
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
                view.loadDataWithBaseURL("https://" + HOST + "/offline", offlineHtml(), "text/html", "utf-8", null);
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
        public void googleSignIn(String clientId) {
            runOnUiThread(() -> startGoogleSignIn(clientId));
        }

        @JavascriptInterface
        public void retry() {
            runOnUiThread(() -> { showingOffline = false; webView.loadUrl(START_URL); });
        }
    }
}
