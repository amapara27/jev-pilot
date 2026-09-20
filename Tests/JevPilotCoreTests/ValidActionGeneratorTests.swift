// Tests candidate generation against deterministic desktop snapshots.
import ApplicationServices
import XCTest

@testable import JevPilotCore

/// Verifies the generator offers only bounded, currently valid actions.
@MainActor
final class ValidActionGeneratorTests: XCTestCase {
  func testGeneratesOnlyEnabledElementActionsAndStop() {
    let state = DesktopState(
      activeApplication: .init(name: "Editor", bundleIdentifier: "test.editor"),
      runningApplications: [
        .init(name: "Editor", bundleIdentifier: "test.editor"),
        .init(name: "Terminal", bundleIdentifier: "test.terminal"),
      ],
      elements: [
        .init(
          id: "enabled", role: kAXButtonRole as String, label: "Run",
          supportedActions: [kAXPressAction as String]),
        .init(
          id: "disabled", role: kAXButtonRole as String, label: "Delete", isEnabled: false,
          supportedActions: [kAXPressAction as String]),
      ],
      isAccessibilityTrusted: true
    )

    let candidates = ValidActionGenerator(supportedApplications: []).candidates(
      for: "click Run", state: state)

    XCTAssertTrue(
      candidates.contains {
        $0.action == .focusApp(bundleIdentifier: "test.terminal", name: "Terminal")
      })
    XCTAssertTrue(
      candidates.contains { $0.action == .clickElement(elementID: "enabled", label: "Run") })
    XCTAssertFalse(
      candidates.contains { $0.action == .clickElement(elementID: "disabled", label: "Delete") })
    XCTAssertTrue(candidates.contains { if case .stop = $0.action { true } else { false } })
    XCTAssertEqual(Set(candidates.map(\.id)).count, candidates.count)
  }

  func testQuotedTypingIsConcreteAndOnlyTargetsFocusedTextField() {
    let state = DesktopState(
      elements: [
        .init(id: "field", role: kAXTextFieldRole as String, label: "Search", isFocused: true)
      ],
      isAccessibilityTrusted: true
    )

    let candidates = ValidActionGenerator(supportedApplications: []).candidates(
      for: "type \"installation guide\"", state: state)

    XCTAssertTrue(
      candidates.contains {
        $0.action == .typeText(elementID: "field", text: "installation guide")
      })
  }

  func testNeverExceedsJevChoiceLimit() {
    let elements = (0..<300).map {
      UIElementState(
        id: "button-\($0)", role: kAXButtonRole as String, label: "Button \($0)",
        supportedActions: [kAXPressAction as String])
    }
    let state = DesktopState(elements: elements, isAccessibilityTrusted: true)
    let candidates = ValidActionGenerator(supportedApplications: []).candidates(
      for: "click a button", state: state)
    XCTAssertLessThanOrEqual(candidates.count, 255)
    XCTAssertTrue(candidates.contains { if case .stop = $0.action { true } else { false } })
  }
}
