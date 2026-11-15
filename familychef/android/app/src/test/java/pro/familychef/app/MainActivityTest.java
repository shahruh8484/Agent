package pro.familychef.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.content.Intent;
import android.net.Uri;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34)
public class MainActivityTest {

    private MainActivity launch(Intent intent) {
        ActivityController<MainActivity> c = Robolectric.buildActivity(MainActivity.class, intent);
        return c.setup().get();
    }

    @Test
    public void startsAndLoadsSite() {
        MainActivity a = launch(new Intent(Intent.ACTION_MAIN));
        assertFalse(a.isFinishing());
        assertEquals(MainActivity.START_URL, shadowOf(a.webView).getLastLoadedUrl());
        assertTrue(a.webView.getSettings().getJavaScriptEnabled());
        assertTrue(a.webView.getSettings().getDomStorageEnabled());
        assertTrue(a.webView.getSettings().getUserAgentString().contains("FamilyChefApp/"));
    }

    @Test
    public void deepLinkOpensThatPage() {
        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse("https://familychef.pro/privacy"));
        MainActivity a = launch(i);
        assertEquals("https://familychef.pro/privacy", shadowOf(a.webView).getLastLoadedUrl());
    }

    @Test
    public void googleSignInRejectsForeignClientId() {
        MainActivity a = launch(new Intent(Intent.ACTION_MAIN));
        a.startGoogleSignIn("evil.example.com");  // must not crash or start anything
        assertFalse(a.isFinishing());
    }

    @Test
    public void offlinePageFollowsLanguage() {
        java.util.Locale.setDefault(java.util.Locale.ENGLISH);
        assertTrue(MainActivity.offlineHtml().contains("No internet connection"));
        java.util.Locale.setDefault(new java.util.Locale("uz"));
        assertTrue(MainActivity.offlineHtml().contains("Нет подключения"));
    }

    @Test
    public void ownSiteStaysInsideOtherLinksGoToBrowser() {
        assertTrue(MainActivity.isOwnSite(Uri.parse("https://familychef.pro/download")));
        assertFalse(MainActivity.isOwnSite(Uri.parse("https://example.com/")));
        assertFalse(MainActivity.isOwnSite(Uri.parse("http://familychef.pro/")));

        MainActivity a = launch(new Intent(Intent.ACTION_MAIN));
        a.openExternal(Uri.parse("https://example.com/"));
        Intent next = shadowOf(a).getNextStartedActivity();
        assertNotNull(next);
        assertEquals(Intent.ACTION_VIEW, next.getAction());
        assertEquals("https://example.com/", next.getDataString());
    }
}
