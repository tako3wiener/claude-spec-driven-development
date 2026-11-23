package example.e2e;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import org.junit.jupiter.api.Test;

/** Java-side evidence recorder contract tests; DEMO_ONLY module, not a G4 test. */
public class EvidenceRecorderContractTest {
  @Test void validStepNames() {
    assertEquals("02-result-verified", EvidenceRecorder.valid("02-result-verified",
        "[0-9][0-9]-[A-Za-z0-9_-]+", "screenshot step"));
  }
  @Test void rejectTraversalStep() {
    assertThrows(IllegalArgumentException.class, () -> EvidenceRecorder.valid(
        "../secret", "[0-9][0-9]-[A-Za-z0-9_-]+", "screenshot step"));
  }
  @Test void rejectInvalidTestCase() {
    assertThrows(IllegalArgumentException.class, () -> EvidenceRecorder.valid(
        "TC-../123", "TC-[0-9]+", "test case id"));
  }
}
