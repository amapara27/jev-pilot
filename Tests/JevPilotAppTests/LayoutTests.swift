// Renders app-owned SwiftUI surfaces offscreen with synthetic state and no desktop automation.
import AppKit
import SwiftUI
import XCTest
import JevPilotCore
@testable import JevPilotApp

@MainActor
private final class PreviewProbe: GoalProbing {
  func probe(goal: String) async throws -> JevGoalProbeResult {
    let stop = ActionCandidate(id: "stop", action: .stop(reason: "done"), criterion: "The goal is already complete.")
    let escape = ActionCandidate(id: "escape", action: .pressKey(.escape), criterion: "Dismiss the open surface.")
    let decision = ActionDecision(
      candidate: escape,
      confidence: 0.81,
      probabilities: ["stop": 0.19, "escape": 0.81],
      model: "jev-preview",
      latencyMilliseconds: 184
    )
    return JevGoalProbeResult(
      goal: goal,
      desktopState: .init(activeApplication: .init(name: "Safari"), isAccessibilityTrusted: true),
      candidates: [stop, escape],
      decision: decision,
      requestMetric: .init(latencyMilliseconds: 184, inputTokens: 620, outputTokens: 8)
    )
  }
}
@MainActor
private final class PreviewSpeech: SpeechProviding {
  var onEvent: ((SpeechEvent) -> Void)?
  func start(preset: SpeechRecognitionPreset) async { onEvent?(.ready) }
  func finish() async { onEvent?(.transcript("Preview transcript", isFinal: true)) }
  func stop() {}
}

/// Opt-in image artifacts let layout QA run without screen recording or Accessibility access.
@MainActor
final class LayoutTests: XCTestCase {
  func testReadinessUsesKeyExistenceWithoutLoadingSecretData() {
    var existenceChecks = 0
    let readiness = Readiness(
      storedKeyStatus: {
        existenceChecks += 1
        return true
      },
      environmentKeyStatus: { false }
    )
    readiness.refresh()
    XCTAssertTrue(readiness.hasStoredKey)
    XCTAssertTrue(readiness.hasKey)
    XCTAssertEqual(existenceChecks, 1)
  }

  func testRenderControlCenterSurfaces() async throws {
    guard let output = ProcessInfo.processInfo.environment["JEV_PREVIEW_OUTPUT"] else {
      throw XCTSkip("Set JEV_PREVIEW_OUTPUT to render offscreen UI artifacts.")
    }
    let destination = URL(fileURLWithPath: output)
    try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
    let store = RunStore(inMemory: true)
    let session = SessionCoordinator(probe: PreviewProbe(), speech: PreviewSpeech(), defaults: nil)
    session.runTyped("Dismiss the current dialog")
    for _ in 0..<100 where session.state != .complete {
      try await Task.sleep(for: .milliseconds(5))
    }
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
      let root = view.environmentObject(session).environmentObject(store).environmentObject(readiness)
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
    session.startListening()
    for _ in 0..<100 where session.state != .listening {
      try await Task.sleep(for: .milliseconds(5))
    }
    try await render("control-listening", ContentView(), width: 1040, height: 730)
    session.stop()
    try await render("usage", UsageView(), width: 780, height: 730)
    try await render("history", HistoryView(), width: 780, height: 650)
    try await render("history-narrow", HistoryView(), width: 600, height: 650)
    try await render("settings", SettingsView(), width: 470, height: 390)
    try await render("settings-dark", SettingsView(), width: 470, height: 390, dark: true)
    try await render("menu-bar", MenuBarPanel(), width: 330, height: 260)
    try await render("menu-bar-dark", MenuBarPanel(), width: 330, height: 260, dark: true)
    try await render("transcript", TranscriptHUD(session: session), width: 440, height: 90)
    try await render("startup-loading", StartupLoadingView(), width: 780, height: 580)
  }
}
