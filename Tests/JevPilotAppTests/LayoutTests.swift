// Renders app-owned SwiftUI surfaces offscreen with synthetic state and no desktop automation.
import AppKit
import SwiftUI
import XCTest
import JevPilotCore
@testable import JevPilotApp

@MainActor
private final class PreviewPerception: DesktopPerceiving {
  func requestAccessibilityPermission(prompt: Bool) -> Bool { false }
  func snapshot(recentActions: [ActionRecord]) throws -> DesktopState { .init() }
}
private struct PreviewDecision: DecisionEngine {
  func decide(goal: String, state: DesktopState, candidates: [ActionCandidate]) async throws -> ActionDecision {
    throw DecisionError.missingAPIKey
  }
}
@MainActor
private final class PreviewExecutor: ActionExecuting {
  func execute(_ action: AutomationAction) async -> ExecutionResult { .init(succeeded: false, message: "Preview") }
}
@MainActor
private final class PreviewSpeech: SpeechProviding {
  var onEvent: ((SpeechEvent) -> Void)?
  func start() async {}
  func stop() {}
}

/// Opt-in image artifacts let layout QA run without screen recording or Accessibility access.
@MainActor
final class LayoutTests: XCTestCase {
  func testRenderControlCenterSurfaces() async throws {
    guard let output = ProcessInfo.processInfo.environment["JEV_PREVIEW_OUTPUT"] else {
      throw XCTSkip("Set JEV_PREVIEW_OUTPUT to render offscreen UI artifacts.")
    }
    let destination = URL(fileURLWithPath: output)
    try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
    let store = RunStore(inMemory: true)
    let controller = AutomationController(perception: PreviewPerception(), decisionEngine: PreviewDecision(), executor: PreviewExecutor(), store: store)
    let session = SessionCoordinator(controller: controller, speech: PreviewSpeech(), defaults: nil)
    let readiness = Readiness()
    readiness.hasKey = true
    readiness.accessibility = true
    var record = RunRecord(command: "Switch to Safari and scroll down")
    record.endedAt = record.startedAt.addingTimeInterval(3.4)
    record.outcome = .completed
    record.events = [.init(kind: .action, title: "Focus Safari", detail: "Executed", succeeded: true), .init(kind: .action, title: "Scroll down", detail: "Executed", succeeded: true), .init(kind: .status, title: "Completed")]
    record.requests = [.init(latencyMilliseconds: 210, inputTokens: 1650, outputTokens: 12)]
    store.upsert(record)
    func render<V: View>(_ name: String, _ view: V, width: CGFloat, height: CGFloat, dark: Bool = false) async throws {
      let root = view.environmentObject(session).environmentObject(controller).environmentObject(store).environmentObject(readiness)
        .environment(\.colorScheme, dark ? .dark : .light)
        .background(dark ? Color(nsColor: .darkGray) : .white)
      let host = NSHostingView(rootView: root)
      host.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
      let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height), styleMask: [.borderless], backing: .buffered, defer: false)
      window.isReleasedWhenClosed = false
      window.contentView = host
      window.appearance = host.appearance
      host.frame = NSRect(x: 0, y: 0, width: width, height: height)
      try await Task.sleep(for: .milliseconds(60))
      host.layoutSubtreeIfNeeded()
      guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { XCTFail("No bitmap for \(name)"); return }
      host.cacheDisplay(in: host.bounds, to: bitmap)
      guard let data = bitmap.representation(using: .png, properties: [:]) else { XCTFail("No PNG for \(name)"); return }
      try data.write(to: destination.appendingPathComponent("\(name).png"))
      // PNG byte size varies with compression and theme; validate the image itself.
      XCTAssertGreaterThanOrEqual(bitmap.pixelsWide, Int(width), name)
      XCTAssertGreaterThanOrEqual(bitmap.pixelsHigh, Int(height), name)
      window.close()
    }
    try await render("control-light", ContentView(), width: 1040, height: 730)
    try await render("control-dark", ContentView(), width: 1040, height: 730, dark: true)
    try await render("control-narrow", ContentView(), width: 780, height: 580)
    try await render("usage", UsageView(), width: 780, height: 730)
    try await render("history", HistoryView(), width: 780, height: 650)
    try await render("history-narrow", HistoryView(), width: 600, height: 650)
    try await render("settings", SettingsView(), width: 610, height: 690)
    try await render("menu-bar", MenuBarPanel(), width: 330, height: 260)
    try await render("menu-bar-dark", MenuBarPanel(), width: 330, height: 260, dark: true)
    try await render("transcript", TranscriptHUD(session: session), width: 440, height: 90)
    try await render("startup-loading", StartupLoadingView(), width: 780, height: 580)
  }
}
