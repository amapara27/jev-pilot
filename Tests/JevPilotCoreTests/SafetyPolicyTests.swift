// Tests the local safety policy's confidence and blocking rules.
import XCTest

@testable import JevPilotCore

/// Verifies policy outcomes without involving the model or desktop.
final class SafetyPolicyTests: XCTestCase {
  private let policy = SafetyPolicy()

  func testLowRiskNeedsMinimumConfidence() {
    let state = DesktopState()
    XCTAssertEqual(
      policy.assess(action: .scrollDown, confidence: 0.9, state: state).disposition, .allow)
    XCTAssertEqual(
      policy.assess(action: .scrollDown, confidence: 0.2, state: state).disposition,
      .requireConfirmation)
  }

  func testSendAndReturnAlwaysRequireConfirmation() {
    let state = DesktopState()
    XCTAssertEqual(
      policy.assess(
        action: .clickElement(elementID: "send", label: "Send Message"), confidence: 1, state: state
      ).disposition,
      .requireConfirmation
    )
    XCTAssertEqual(
      policy.assess(action: .pressKey(.returnKey), confidence: 1, state: state).disposition,
      .requireConfirmation
    )
  }

  func testSecureFieldsAndPurchasesAreBlocked() {
    let state = DesktopState(elements: [
      .init(id: "password", role: "AXTextField", subrole: "AXSecureTextField", label: "Password")
    ])
    XCTAssertEqual(
      policy.assess(
        action: .typeText(elementID: "password", text: "secret"), confidence: 1, state: state
      ).disposition,
      .deny
    )
    XCTAssertEqual(
      policy.assess(
        action: .clickElement(elementID: "buy", label: "Buy Now"), confidence: 1, state: state
      ).disposition,
      .deny
    )
  }

  func testSensitiveGoalIsBlockedBeforeProviderRequest() {
    XCTAssertNotNil(policy.blockedReason(forGoal: "type my password into the login field"))
    XCTAssertNil(policy.blockedReason(forGoal: "switch back to Terminal"))
  }
}
