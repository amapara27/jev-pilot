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
    XCTAssertLessThanOrEqual(candidates.count, 80)
    XCTAssertTrue(candidates.contains { if case .stop = $0.action { true } else { false } })
  }

  func testNaturalTypingAndTerminalToggle() {
    let state = DesktopState(
      activeApplication: .init(name: "Terminal", bundleIdentifier: "com.apple.Terminal"),
      elements: [.init(id: "field", role: kAXTextAreaRole as String, isFocused: true)],
      isAccessibilityTrusted: true)
    let off = ValidActionGenerator(supportedApplications: [])
    XCTAssertTrue(off.candidates(for: "type hello world", state: state).contains {
      $0.action == .typeText(elementID: "field", text: "hello world")
    })
    XCTAssertTrue(off.candidates(for: "run git status", state: state).contains { $0.action == .terminalType(command: "git status") })
    XCTAssertFalse(off.candidates(for: "run git status", state: state).contains { $0.action == .terminalRun(command: "git status") })
    let on = ValidActionGenerator(supportedApplications: [], terminalExecutionEnabled: { true })
    XCTAssertTrue(on.candidates(for: "run git status", state: state).contains { $0.action == .terminalRun(command: "git status") })
  }

  func testCommonSpokenAppAliasCanLaunchNonRunningApp() {
    let generator = ValidActionGenerator(supportedApplications: [.init(name: "Google Chrome",
      bundleIdentifiers: ["com.google.Chrome"])], applicationURL: { _ in URL(fileURLWithPath: "/Applications/Google Chrome.app") })
    let candidates = generator.candidates(for: "open Chrome", state: DesktopState())
    XCTAssertTrue(candidates.contains { $0.action == .openApp(bundleIdentifier: "com.google.Chrome", name: "Google Chrome") })
  }

  func testFinderItemRequiresUniqueURLAndSupportsSelectedFileOperations() {
    let item = UIElementState(id: "item", role: kAXRowRole as String, label: "notes.txt", url: "/tmp/notes.txt")
    let finder = DesktopState(activeApplication: .init(name: "Finder", bundleIdentifier: "com.apple.finder"), elements: [item])
    let generator = ValidActionGenerator(supportedApplications: [])
    XCTAssertTrue(generator.candidates(for: "select notes.txt", state: finder).contains { $0.action == .finderSelectItem(elementID: "item", url: "/tmp/notes.txt") })
    var ambiguous = finder
    ambiguous.elements.append(.init(id: "other", role: kAXRowRole as String, label: "notes.txt", url: "/tmp/other/notes.txt"))
    XCTAssertFalse(generator.candidates(for: "select notes.txt", state: ambiguous).contains { if case .finderSelectItem = $0.action { true } else { false } })
    let selected = DesktopState(activeApplication: finder.activeApplication, elements: [
      .init(id: "item", role: kAXRowRole as String, label: "notes.txt", url: "/tmp/notes.txt", isSelected: true)
    ])
    XCTAssertTrue(generator.candidates(for: "rename notes.txt to done.txt", state: selected).contains { $0.action == .finderRenameItem(elementID: "item", url: "/tmp/notes.txt", newName: "done.txt") })
    XCTAssertTrue(generator.candidates(for: "move notes.txt to Downloads", state: selected).contains {
      $0.action == .finderMoveItem(elementID: "item", url: "/tmp/notes.txt", destination: NSHomeDirectory() + "/Downloads")
    })
  }

  func testWindowStatesAndDuplicateInstalledAppNames() {
    let state = DesktopState(
      windows: [.init(id: "window.0", title: "Draft", role: "AXWindow", isFocused: true, isFullScreen: false),
        .init(id: "window.1", title: "Hidden", role: "AXWindow", isFocused: false, isMinimized: true)],
      focusedWindowID: "window.0")
    let generator = ValidActionGenerator(supportedApplications: [
      .init(name: "Editor", bundleIdentifiers: ["one.editor", "two.editor"])
    ], applicationURL: { URL(fileURLWithPath: "/Applications/\($0).app") })
    let actions = generator.candidates(for: "open Editor", state: state).map(\.action)
    XCTAssertTrue(actions.contains(.enterFullScreen(windowID: "window.0", title: "Draft")))
    XCTAssertTrue(actions.contains(.restoreWindow(windowID: "window.1", title: "Hidden")))
    XCTAssertTrue(actions.contains(.openApp(bundleIdentifier: "one.editor", name: "Editor")))
    XCTAssertTrue(actions.contains(.openApp(bundleIdentifier: "two.editor", name: "Editor")))
  }
}
