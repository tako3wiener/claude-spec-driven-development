package example.e2e;

import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.Tracing;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Objects;

/**
 * Example evidence collector for a single E2E test case.
 * Tracing.start() must be called on the context before the first browser action.
 * Screenshots and traces can contain sensitive information. Use synthetic data only.
 */
public final class EvidenceRecorder {
  private final String changeId;
  private final String runId;
  private final String tcId;
  private final String browser;
  private final String environmentOrigin;
  private final String gitCommit;
  private final String snapshotId;
  private final String runnerTokenDigest;
  private final Path caseDir;
  private final Page page;
  private final BrowserContext context;
  private final List<Screenshot> screenshots = new ArrayList<>();
  private boolean traceStarted;

  public EvidenceRecorder(String changeId, String runId, String tcId,
                          String browser, String environmentOrigin,
                          BrowserContext context, Page page) throws IOException {
    this.changeId = valid(changeId, "CHG-[A-Za-z0-9_-]+", "change id");
    this.runId = valid(runId, "[A-Za-z0-9][A-Za-z0-9_-]*", "run id");
    this.tcId = valid(tcId, "TC-[0-9]+", "test case id");
    this.browser = Objects.requireNonNull(browser);
    this.environmentOrigin = Objects.requireNonNull(environmentOrigin);
    this.gitCommit = requireEnv("E2E_GIT_COMMIT");
    this.snapshotId = valid(requireEnv("E2E_SNAPSHOT_ID"), "[0-9a-f]{64}", "code snapshot");
    this.runnerTokenDigest = valid(requireEnv("E2E_RUN_TOKEN_DIGEST"), "[0-9a-f]{64}", "runner digest");
    this.context = Objects.requireNonNull(context);
    this.page = Objects.requireNonNull(page);
    String root = System.getenv("E2E_EVIDENCE_ROOT");
    if (root == null || root.isBlank()) {
      throw new IllegalStateException("E2E_EVIDENCE_ROOT must point to the configured E2E evidence directory");
    }
    this.caseDir = Path.of(root).toAbsolutePath().normalize()
        .resolve(Path.of(changeId, runId, tcId));
    if (Files.exists(caseDir)) {
      throw new IOException("Evidence directory already exists; use a unique E2E_RUN_ID: " + caseDir);
    }
    Files.createDirectory(caseDir);
  }

  public void startTrace() {
    context.tracing().start(new Tracing.StartOptions()
        .setScreenshots(true)
        .setSnapshots(true));
    traceStarted = true;
  }

  public void capture(String step, boolean fullPage) throws IOException {
    String safeStep = valid(step, "[0-9][0-9]-[A-Za-z0-9_-]+", "screenshot step");
    String name = safeStep + ".png";
    Path out = caseDir.resolve(name);
    if (Files.exists(out)) {
      throw new IOException("Screenshot already exists: " + out);
    }
    // App should mark truly sensitive regions with data-e2e-sensitive.
    // This does not redact the DOM/network contained in the trace!
    page.screenshot(new Page.ScreenshotOptions()
        .setPath(out)
        .setFullPage(fullPage)
        .setMask(List.of(page.locator("[data-e2e-sensitive]")))
        .setMaskColor("#333333"));
    screenshots.add(new Screenshot(safeStep, name, sha256(out)));
  }

  /** Always call this in finally. A failed trace or missing evidence must not become PASS. */
  public void finish(boolean assertionSucceeded, Throwable testFailure) throws IOException {
    RuntimeException traceFailure = null;
    if (traceStarted) {
      try {
        context.tracing().stop(new Tracing.StopOptions().setPath(caseDir.resolve("trace.zip")));
      } catch (RuntimeException ex) {
        traceFailure = ex;
      }
    }
    boolean verified = screenshots.stream().anyMatch(s -> s.step.endsWith("-result-verified"));
    boolean hasTrace = Files.isRegularFile(caseDir.resolve("trace.zip"));
    String status = assertionSucceeded && verified && hasTrace && traceFailure == null ? "PASS" : "FAIL";
    String failureType = testFailure != null ? testFailure.getClass().getSimpleName()
        : traceFailure != null ? traceFailure.getClass().getSimpleName()
        : assertionSucceeded && !verified ? "MissingVerifiedScreenshot"
        : assertionSucceeded && !hasTrace ? "MissingTrace" : "AssertionFailure";
    Path manifest = caseDir.resolve("manifest.json");
    Files.writeString(manifest, manifestJson(status, failureType, hasTrace), StandardCharsets.UTF_8);
    if ("FAIL".equals(status) && testFailure == null) {
      throw new IllegalStateException("E2E evidence incomplete for " + tcId + ": " + failureType);
    }
  }

  private String manifestJson(String result, String failureType, boolean hasTrace) {
    StringBuilder sb = new StringBuilder();
    sb.append("{\n")
      .append("  \"schema_version\": 2,\n")
      .append("  \"change_id\": \"").append(escape(changeId)).append("\",\n")
      .append("  \"run_id\": \"").append(escape(runId)).append("\",\n")
      .append("  \"tc_id\": \"").append(escape(tcId)).append("\",\n")
      .append("  \"result\": \"").append(result).append("\",\n")
      .append("  \"captured_at_utc\": \"").append(Instant.now()).append("\",\n")
      .append("  \"browser\": \"").append(escape(browser)).append("\",\n")
      .append("  \"environment_origin\": \"").append(escape(environmentOrigin)).append("\",\n")
      .append("  \"git_commit\": \"").append(escape(gitCommit)).append("\",\n")
      .append("  \"snapshot_id\": \"").append(snapshotId).append("\",\n")
      .append("  \"runner_token_digest\": \"").append(runnerTokenDigest).append("\",\n")
      .append("  \"trace_sha256\": ").append(hasTrace ? "\"" + sha256Unchecked(caseDir.resolve("trace.zip")) + "\"" : "null").append(",\n")
      .append("  \"failure_type\": \"").append("FAIL".equals(result) ? escape(failureType) : "").append("\",\n")
      .append("  \"trace_file\": ").append(hasTrace ? "\"trace.zip\"" : "null").append(",\n")
      .append("  \"screenshots\": [\n");
    for (int i = 0; i < screenshots.size(); i++) {
      Screenshot s = screenshots.get(i);
      sb.append("    {\"step\": \"").append(escape(s.step)).append("\", ")
        .append("\"file\": \"").append(escape(s.file)).append("\", ")
        .append("\"sha256\": \"").append(s.sha256).append("\"}");
      if (i < screenshots.size() - 1) sb.append(',');
      sb.append('\n');
    }
    sb.append("  ]\n}\n");
    return sb.toString();
  }

  private static String requireEnv(String name) {
    String value = System.getenv(name);
    if (value == null || value.isBlank()) throw new IllegalStateException(name + " is required");
    return value;
  }

  private static String sha256Unchecked(Path path) {
    try { return sha256(path); }
    catch (IOException ex) { throw new IllegalStateException("Trace hash unavailable", ex); }
  }

  static String valid(String value, String regex, String label) {
    if (value == null || !value.matches(regex)) {
      throw new IllegalArgumentException("Invalid " + label + ": " + value);
    }
    return value;
  }

  private static String escape(String s) {
    return s.replace("\\", "\\\\").replace("\"", "\\\"")
        .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t");
  }

  private static String sha256(Path file) throws IOException {
    try {
      return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file)));
    } catch (NoSuchAlgorithmException e) {
      throw new IllegalStateException("SHA-256 unavailable", e);
    }
  }

  private static final class Screenshot {
    final String step;
    final String file;
    final String sha256;
    Screenshot(String step, String file, String sha256) {
      this.step = step;
      this.file = file;
      this.sha256 = sha256;
    }
  }
}
