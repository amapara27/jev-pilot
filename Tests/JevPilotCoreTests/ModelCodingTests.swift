// Tests that shared action models retain their data through JSON encoding.
import XCTest

@testable import JevPilotCore

/// Verifies the action contract remains serializable.
final class ModelCodingTests: XCTestCase {
  func testEveryActionRoundTripsThroughJSON() throws {
    let actions: [AutomationAction] = [
      .openApp(bundleIdentifier: "app", name: "App"),
      .focusApp(bundleIdentifier: "app", name: "App"),
      .closeWindow(windowID: "window", title: "Title"),
      .clickElement(elementID: "button", label: "Run"),
      .focusElement(elementID: "field", label: "Search"),
      .typeText(elementID: "field", text: "exact text"),
      .pressKey(.escape),
      .scrollUp,
      .scrollDown,
      .stop(reason: "done"),
    ]

    let data = try JSONEncoder().encode(actions)
    XCTAssertEqual(try JSONDecoder().decode([AutomationAction].self, from: data), actions)
  }
}
