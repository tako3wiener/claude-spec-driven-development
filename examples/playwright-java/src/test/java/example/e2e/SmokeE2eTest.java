package example.e2e;

import com.microsoft.playwright.Browser;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.Playwright;
import org.junit.jupiter.api.Assertions;
import org.junit.jupiter.api.Test;

import java.net.URI;

/**
 * DEMO_ONLY: never treat this smoke test as approved business E2E.
 * Environment variables must point to an isolated test system, never production.
 */
public class SmokeE2eTest {
  @Test
  void TC_001_topPageIsVisible() throws Throwable {
    String baseUrl = requireEnv("E2E_BASE_URL");
    String allowedOrigin = requireEnv("E2E_ALLOWED_ORIGIN");
    String changeId = requireEnv("E2E_CHANGE_ID");
    String runId = requireEnv("E2E_RUN_ID");
    requireEnv("E2E_GIT_COMMIT");
    requireEnv("E2E_SNAPSHOT_ID");
    requireEnv("E2E_RUN_TOKEN_DIGEST");
    requireEnv("E2E_EVIDENCE_ROOT");
    ensureAllowedOrigin(baseUrl, allowedOrigin);

    try (Playwright playwright = Playwright.create()) {
      Browser browser = playwright.chromium().launch(
          new com.microsoft.playwright.BrowserType.LaunchOptions().setHeadless(true));
      try {
        BrowserContext context = browser.newContext(new Browser.NewContextOptions()
            .setViewportSize(1365, 768)
            .setLocale("ja-JP")
            .setTimezoneId("Asia/Tokyo"));
        try {
          // Supplementary same-origin browser guard. External firewall/container
          // egress restrictions remain mandatory (this cannot cover backend traffic).
          context.route("**/*", route -> {
            try {
              ensureAllowedOrigin(route.request().url(), allowedOrigin);
              route.resume();
            } catch (RuntimeException ex) {
              route.abort();
            }
          });
          Page page = context.newPage();
          EvidenceRecorder evidence = new EvidenceRecorder(
              changeId, runId, "TC-001", "chromium", origin(baseUrl), context, page);
          Throwable failure = null;
          evidence.startTrace(); // Start before navigation or other browser actions.
          try {
            page.navigate(baseUrl);
            ensureAllowedOrigin(page.url(), allowedOrigin); // Detect redirects to a different origin.
            evidence.capture("01-initial", false);

            // TODO: replace with approved business-specific assertions (REQ/TC).
            Assertions.assertTrue(page.locator("body").isVisible(), "main page is not visible");
            ensureAllowedOrigin(page.url(), allowedOrigin);
            evidence.capture("02-result-verified", true);
          } catch (Throwable ex) {
            failure = ex;
            try {
              evidence.capture("99-failure", true);
            } catch (Throwable captureError) {
              ex.addSuppressed(captureError);
            }
            throw ex;
          } finally {
            try {
              evidence.finish(failure == null, failure);
            } catch (Throwable finishError) {
              if (failure == null) throw finishError;
              failure.addSuppressed(finishError);
            }
          }
        } finally {
          context.close();
        }
      } finally {
        browser.close();
      }
    }
  }

  private static String requireEnv(String name) {
    String value = System.getenv(name);
    if (value == null || value.isBlank()) {
      throw new IllegalStateException(name + " must be configured for an isolated test environment");
    }
    return value;
  }

  /** Explicit origin allowlist protects against accidental use of a production URL. */
  private static void ensureAllowedOrigin(String actual, String allowed) {
    if (!origin(actual).equals(origin(allowed))) {
      throw new IllegalStateException("E2E_BASE_URL origin does not match E2E_ALLOWED_ORIGIN");
    }
  }

  private static String origin(String target) {
    URI uri = URI.create(target);
    if (!"https".equalsIgnoreCase(uri.getScheme()) && !"http".equalsIgnoreCase(uri.getScheme())) {
      throw new IllegalArgumentException("Only HTTP(S) origins are permitted");
    }
    if (uri.getHost() == null || uri.getUserInfo() != null) {
      throw new IllegalArgumentException("URL with missing host or user-info is forbidden");
    }
    return uri.getScheme().toLowerCase() + "://" + uri.getHost().toLowerCase()
        + (uri.getPort() < 0 ? "" : ":" + uri.getPort());
  }
}
