// Runs opt-in, real desktop acceptance checks inside the signed app's permission identity.
import AppKit
import ApplicationServices
import JevPilotCore

/// No mocks: the shared session, Jev provider, AX perception, executor and verifier run here.
@MainActor
enum NativeEndToEndRun {
  static var outputDirectory: URL? {
    let args = ProcessInfo.processInfo.arguments
    guard let index = args.firstIndex(of: "--jev-e2e-output"), args.indices.contains(index + 1),
      args[index + 1].hasPrefix("/") else { return nil }
    return URL(fileURLWithPath: args[index + 1], isDirectory: true)
  }

  struct Scenario: Codable {
    let command: String
    let passed: Bool
    let assertions: [String: Bool]
    let run: RunRecord?
  }
  struct Report: Codable {
    let startedAt: Date
    let passed: Bool
    let error: String?
    let scenarios: [Scenario]
  }

  /// Test notes/files remain for inspection; never delete or submit anything automatically.
  static func run(model: AppModel) async {
    guard let directory = outputDirectory else { return }
    let started = Date()
    var results: [Scenario] = []
    var failure: String?
    do {
      try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
      guard AXIsProcessTrusted() else { throw CheckFailure("Enable Accessibility for this Jev Pilot bundle, then rerun. No desktop effects were performed.") }
      guard model.readiness.hasKey else { throw CheckFailure("Configure a TypeSafe key or use the explicit development .env launcher.") }
      let token = String(UUID().uuidString.prefix(8))
      let marker = "Jev acceptance \(token)"
      let literal = "bread and butter \(token)"
      let file = "jev-resumes-\(token).txt"
      let fixtures = directory.appendingPathComponent("fixtures", isDirectory: true)
      try FileManager.default.createDirectory(at: fixtures, withIntermediateDirectories: true)
      try Data("Disposable Jev acceptance fixture.\n".utf8).write(to: fixtures.appendingPathComponent(file), options: .atomic)
      let cases = [
        ("open Notes create a new note and type \(marker)", "com.apple.Notes", marker, [ActionKind.notesCreateNote, .typeText]),
        ("type \(literal) in a new note", "com.apple.Notes", literal, [.notesCreateNote, .typeText]),
        ("open Finder and open folder \(fixtures.path) and locate \(file)", "com.apple.finder", file, [.finderOpenFolder, .searchInApp]),
        ("open Terminal and type pwd", "com.apple.Terminal", "pwd", [.terminalType]),
      ]
      for (command, bundle, expected, requiredKinds) in cases {
        model.session.stop()
        model.session.runTyped(command)
        let deadline = ContinuousClock.now.advanced(by: .seconds(45))
        while model.session.activeGoal != nil, ContinuousClock.now < deadline {
          if let pending = model.controller.pendingConfirmation {
            // The explicit test authorizes only its known benign operations, never shell Run.
            guard mayApprove(pending.decision.candidate.action, expected: expected, folder: fixtures.path) else {
              throw CheckFailure("Unexpected action requires manual review: \(pending.decision.candidate.action.summary)")
            }
            model.session.confirmPendingAction()
          }
          try await Task.sleep(for: .milliseconds(50))
        }
        if model.session.activeGoal != nil { model.session.stop(); throw CheckFailure("Scenario timed out: \(command)") }
        let controller = model.controller
        let state = controller.latestState
        let actions = controller.history.filter(\.succeeded).map(\.action)
        let textMatches: Bool
        if bundle == "com.apple.Notes" {
          textMatches = state?.elements.contains { $0.isFocused && $0.role == "AXTextArea" && $0.value == expected } == true
        } else if bundle == "com.apple.finder" {
          textMatches = state?.elements.contains { $0.isFocused && $0.value == expected } == true
        } else {
          textMatches = actions.contains(.terminalType(command: expected))
        }
        let assertions = [
          "completed": controller.currentRun?.outcome == .completed,
          "correct_app": state?.activeApplication?.bundleIdentifier == bundle,
          "required_effects_verified": requiredKinds.allSatisfy { kind in actions.contains { $0.kind == kind } },
          "exact_payload_verified": textMatches,
          "no_submission": !actions.contains { $0.kind == .terminalRun || $0 == .pressKey(.returnKey) },
          "one_run_record": model.store.records.filter { $0.id == controller.currentRun?.id }.count == 1,
        ]
        let passed = assertions.values.allSatisfy { $0 }
        results.append(.init(command: command, passed: passed, assertions: assertions, run: controller.currentRun))
        if !passed { throw CheckFailure("Scenario failed: \(controller.status.label)") }
      }
    } catch { failure = error.localizedDescription }
    model.session.stop()
    let report = Report(startedAt: started, passed: failure == nil && results.count == 4,
      error: failure, scenarios: results)
    do {
      let encoder = JSONEncoder()
      encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
      encoder.dateEncodingStrategy = .iso8601
      try encoder.encode(report).write(to: directory.appendingPathComponent("report.json"), options: .atomic)
    } catch { NSLog("Could not save Jev E2E report: %@", error.localizedDescription) }
    NSApp.terminate(nil)
  }

  private static func mayApprove(_ action: AutomationAction, expected: String, folder: String) -> Bool {
    switch action {
    case .openApp(let id, _), .focusApp(let id, _): ["com.apple.Notes", "com.apple.finder", "com.apple.Terminal"].contains(id)
    case .notesCreateNote, .focusElement: true
    case .typeText(_, let text), .terminalType(let text), .searchInApp(let text): text == expected
    case .finderOpenFolder(let path): path == folder
    default: false
    }
  }

  private struct CheckFailure: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
  }
}
