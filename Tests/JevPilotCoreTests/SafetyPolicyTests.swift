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

  func testSpaceCloseAndDeleteControlsRequireConfirmation() {
    let policy = SafetyPolicy()
    let state = DesktopState(isAccessibilityTrusted: true)
    let actions: [AutomationAction] = [
      .pressKey(.space),
      .closeWindow(windowID: "window.0", title: "Draft"),
      .clickElement(elementID: "ax:root.0", label: "Delete item"),
    ]
    for action in actions {
      XCTAssertEqual(policy.assess(action: action, confidence: 1, state: state).disposition, .requireConfirmation)
    }
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

  func testTerminalExecutionAndFinderMoveAlwaysRequireApproval() {
    let state = DesktopState()
    XCTAssertEqual(policy.assess(action: .terminalType(command: "pwd"), confidence: 1, state: state).disposition, .allow)
    for action: AutomationAction in [
      .terminalRun(command: "pwd"),
      .finderMoveItem(elementID: "item", url: "/tmp/a", destination: "/tmp/b"),
      .finderRenameItem(elementID: "item", url: "/tmp/a", newName: "b"),
    ] {
      XCTAssertEqual(policy.assess(action: action, confidence: 1, state: state).disposition, .requireConfirmation)
    }
  }

  func testMultilineTerminalTextCannotBypassRunApproval() {
    let state = DesktopState()
    for action: AutomationAction in [
      .terminalType(command: "echo safe\nrm -f file"),
      .terminalRun(command: "echo safe\nrm -f file"),
    ] {
      XCTAssertEqual(policy.assess(action: action, confidence: 1, state: state).disposition, .deny)
    }
    let terminal = DesktopState(activeApplication: .init(name: "Terminal",
      bundleIdentifier: "com.apple.Terminal", processIdentifier: 1))
    XCTAssertEqual(policy.assess(action: .typeText(elementID: "field", text: "pwd\n"),
      confidence: 1, state: terminal).disposition, .deny)
  }

  func testDestructiveMenuAndExecutableFinderItemRequireApproval() {
    let state = DesktopState()
    XCTAssertEqual(policy.assess(action: .activateMenu(elementID: "menu", label: "Delete"),
      confidence: 1, state: state).disposition, .requireConfirmation)
    XCTAssertEqual(policy.assess(action: .finderOpenItem(elementID: "app", url: "/tmp/Test.app"),
      confidence: 1, state: state).disposition, .requireConfirmation)
    XCTAssertEqual(policy.assess(action: .typeText(elementID: "field", text: "hello\n"),
      confidence: 1, state: state).disposition, .requireConfirmation)
  }
}
