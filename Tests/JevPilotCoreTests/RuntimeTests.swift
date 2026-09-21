// Exercises cancellation, confirmation, speech boundaries, and local history without desktop effects.
import XCTest
@testable import JevPilotCore

@MainActor
private final class FakePerception: DesktopPerceiving {
  var state = DesktopState(activeApplication: .init(name: "Editor", bundleIdentifier: "test.editor", processIdentifier: 123), isAccessibilityTrusted: true)
  func requestAccessibilityPermission(prompt: Bool) -> Bool { true }
  func snapshot(recentActions: [ActionRecord]) throws -> DesktopState { state }
}

@MainActor
private final class FakeExecutor: ActionExecuting {
  var actions: [AutomationAction] = []
  var succeeds = true
  func execute(_ action: AutomationAction) async -> ExecutionResult {
    actions.append(action)
    return .init(succeeded: succeeds, message: succeeds ? "Executed" : "Failed")
  }
}

/// Can deliberately ignore cancellation to reproduce a response arriving after Stop.
private actor FakeEngine: DecisionEngine {
  enum Selection: Sendable { case stop, escape, enter, failing }
  var calls = 0
  var selection: Selection
  var continuation: CheckedContinuation<Void, Never>?
  let delayed: Bool
  init(_ selection: Selection = .stop, delayed: Bool = false) { self.selection = selection; self.delayed = delayed }
  func release() { continuation?.resume(); continuation = nil }
  func decide(goal: String, state: DesktopState, candidates: [ActionCandidate]) async throws -> ActionDecision {
    calls += 1
    if delayed { await withCheckedContinuation { continuation = $0 } }
    if selection == .failing { throw DecisionError.transport("offline") }
    let selected = candidates.first { candidate in
      switch selection {
      case .stop: if case .stop = candidate.action { return true }; return false
      case .escape: return candidate.action == .pressKey(.escape)
      case .enter: return candidate.action == .pressKey(.returnKey)
      case .failing: return false
      }
    }!
    return ActionDecision(candidate: selected, confidence: 1, probabilities: [selected.id: 1], model: "fake", latencyMilliseconds: 12)
  }
}

@MainActor
private final class FakeSpeech: SpeechProviding {
  var onEvent: ((SpeechEvent) -> Void)?
  var starts = 0
  var stops = 0
  func start() async { starts += 1 }
  func stop() { stops += 1 }
  func emit(_ text: String, final: Bool = false) { onEvent?(.transcript(text, isFinal: final)) }
}

@MainActor
final class RuntimeTests: XCTestCase {
  private func controller(_ engine: FakeEngine, perception: FakePerception = .init(), executor: FakeExecutor = .init(), maximumSteps: Int = 12) -> AutomationController {
    AutomationController(perception: perception, actionGenerator: .init(supportedApplications: []), decisionEngine: engine, executor: executor, maximumSteps: maximumSteps, stabilizationMilliseconds: 0)
  }
  private func eventually(_ predicate: @MainActor () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
    for _ in 0..<300 {
      if predicate() { return }
      try? await Task.sleep(for: .milliseconds(5))
    }
    XCTFail("Condition was not reached", file: file, line: line)
  }

  func testStopPreventsLateDecisionFromExecuting() async {
    let engine = FakeEngine(.escape, delayed: true)
    let executor = FakeExecutor()
    let runtime = controller(engine, executor: executor)
    runtime.run(goal: "press escape")
    await eventually { runtime.currentRun?.requests.count == 1 }
    runtime.cancel()
    await engine.release()
    await eventually { runtime.currentRun?.requests.first?.isComplete == true }
    XCTAssertTrue(executor.actions.isEmpty)
    XCTAssertEqual(runtime.status, .stopped)
    XCTAssertEqual(runtime.currentRun?.outcome, .stopped)
  }

  func testLateResponseCannotOverwriteNewBlockedRun() async {
    let engine = FakeEngine(.escape, delayed: true)
    let runtime = controller(engine)
    runtime.run(goal: "press escape")
    await eventually { runtime.currentRun?.requests.count == 1 }
    let oldID = runtime.currentRun!.id
    runtime.cancel()
    runtime.run(goal: "buy a laptop with my credit card")
    let newID = runtime.currentRun!.id
    await engine.release()
    await eventually { runtime.store.records.first(where: { $0.id == oldID })?.requests.first?.isComplete == true }
    XCTAssertEqual(runtime.currentRun?.id, newID)
    XCTAssertEqual(runtime.currentRun?.outcome, .blocked)
    XCTAssertEqual(runtime.currentRun?.requests.count, 0)
  }

  func testStopClearsConfirmationAndApprovalCannotExecute() async {
    let executor = FakeExecutor()
    let runtime = controller(FakeEngine(.enter), executor: executor)
    runtime.run(goal: "press Return")
    await eventually { runtime.pendingConfirmation != nil }
    runtime.cancel()
    runtime.confirmPendingAction()
    XCTAssertNil(runtime.pendingConfirmation)
    XCTAssertEqual(runtime.status, .stopped)
    XCTAssertTrue(executor.actions.isEmpty)
  }

  func testChangedTargetFailsConfirmation() async {
    let perception = FakePerception()
    let executor = FakeExecutor()
    let runtime = controller(FakeEngine(.enter), perception: perception, executor: executor)
    runtime.run(goal: "press Return")
    await eventually { runtime.pendingConfirmation != nil }
    perception.state.activeApplication = .init(name: "Other", processIdentifier: 456)
    runtime.confirmPendingAction()
    await eventually { runtime.currentRun?.outcome != nil }
    XCTAssertEqual(runtime.currentRun?.outcome, .failed)
    XCTAssertTrue(executor.actions.isEmpty)
  }

  func testConfirmationExecutesOnceAndPreservesStepLimit() async {
    let executor = FakeExecutor()
    let runtime = controller(FakeEngine(.enter), executor: executor, maximumSteps: 1)
    runtime.run(goal: "press Return")
    await eventually { runtime.pendingConfirmation != nil }
    runtime.confirmPendingAction()
    runtime.confirmPendingAction()
    await eventually { runtime.currentRun?.outcome != nil }
    XCTAssertEqual(executor.actions, [.pressKey(.returnKey)])
    XCTAssertEqual(runtime.currentRun?.outcome, .failed)
  }

  func testRejectAndExecutorFailureHaveExplicitOutcomes() async {
    let runtime = controller(FakeEngine(.enter))
    runtime.run(goal: "press Return")
    await eventually { runtime.pendingConfirmation != nil }
    runtime.rejectPendingAction()
    XCTAssertEqual(runtime.currentRun?.outcome, .rejected)
    let executor = FakeExecutor()
    executor.succeeds = false
    let failing = controller(FakeEngine(.escape), executor: executor)
    failing.run(goal: "press escape")
    await eventually { failing.currentRun?.outcome != nil }
    XCTAssertEqual(failing.currentRun?.outcome, .failed)
    XCTAssertEqual(failing.currentRun?.actionCount, 0)
  }

  func testFinalAndSilenceDoNotSubmitTwice() async {
    let engine = FakeEngine()
    let runtime = controller(engine)
    let speech = FakeSpeech()
    let session = SessionCoordinator(controller: runtime, speech: speech, defaults: nil, silenceMilliseconds: 20)
    session.startListening()
    await eventually { speech.starts == 1 }
    speech.emit("switch to Safari")
    speech.emit("switch to Safari", final: true)
    speech.emit("switch to Safari", final: true)
    await eventually { runtime.currentRun?.outcome == .completed }
    try? await Task.sleep(for: .milliseconds(35))
    let calls = await engine.calls
    XCTAssertEqual(calls, 1)
    XCTAssertEqual(session.state, .stopped)
    XCTAssertEqual(runtime.store.records.count, 1)
  }

  func testSilenceSubmitsWithoutFinalResult() async {
    let runtime = controller(FakeEngine())
    let speech = FakeSpeech()
    let session = SessionCoordinator(controller: runtime, speech: speech, defaults: nil, silenceMilliseconds: 10)
    session.startListening()
    speech.emit("switch to Safari")
    await eventually { runtime.currentRun?.outcome == .completed }
    XCTAssertEqual(runtime.transcript, "switch to Safari")
  }

  func testStopDiscardsPartialAndPreviousCaptureCallbacks() async {
    let runtime = controller(FakeEngine())
    let speech = FakeSpeech()
    let session = SessionCoordinator(controller: runtime, speech: speech, defaults: nil, silenceMilliseconds: 20)
    session.startListening()
    let oldCallback = speech.onEvent
    speech.emit("partial")
    session.stop()
    session.startListening()
    oldCallback?(.transcript("late final", isFinal: true))
    try? await Task.sleep(for: .milliseconds(35))
    XCTAssertNil(runtime.currentRun)
    XCTAssertEqual(session.state, .listening)
    session.stop()
  }

  func testContinuousResumesOnlyAfterSuccess() async {
    let runtime = controller(FakeEngine())
    let speech = FakeSpeech()
    let session = SessionCoordinator(controller: runtime, speech: speech, defaults: nil)
    session.mode = .continuous
    session.startListening()
    await eventually { speech.starts == 1 }
    speech.emit("switch to Safari", final: true)
    await eventually { speech.starts == 2 }
    XCTAssertEqual(session.state, .listening)
    session.stop()
    XCTAssertEqual(session.state, .stopped)
  }

  func testContinuousPausesForConfirmationAndDoesNotResumeAfterRejection() async {
    let runtime = controller(FakeEngine(.enter))
    let speech = FakeSpeech()
    let session = SessionCoordinator(controller: runtime, speech: speech, defaults: nil)
    session.mode = .continuous
    session.startListening()
    await eventually { speech.starts == 1 }
    speech.emit("press Return", final: true)
    await eventually { session.state == .awaitingConfirmation }
    XCTAssertEqual(speech.starts, 1)
    session.reject()
    XCTAssertEqual(session.state, .stopped)
    XCTAssertEqual(runtime.currentRun?.outcome, .rejected)
  }

  func testReadinessAndTargetFailureNeverCallProvider() async {
    let engine = FakeEngine()
    let runtime = controller(engine)
    let speech = FakeSpeech()
    let blocked = SessionCoordinator(controller: runtime, speech: speech, defaults: nil, readiness: { "Missing key" })
    blocked.startListening()
    XCTAssertEqual(blocked.state, .error("Missing key"))
    XCTAssertEqual(speech.starts, 0)
    let targetFailure = SessionCoordinator(controller: runtime, speech: speech, defaults: nil, prepareTarget: { _ in throw CocoaError(.fileNoSuchFile) })
    targetFailure.runTyped("switch to Safari")
    await eventually { if case .error = targetFailure.state { true } else { false } }
    let calls = await engine.calls
    XCTAssertEqual(calls, 0)
    XCTAssertNil(runtime.currentRun)
  }
}

@MainActor
final class RunStoreTests: XCTestCase {
  func testArchiveRecoversInterruptedRunAndPreservesCompleted() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let url = folder.appendingPathComponent("runs.json")
    let archive = RunArchive(url: url)
    let active = RunRecord(command: "scroll down")
    var complete = RunRecord(command: "switch app")
    complete.outcome = .completed
    complete.endedAt = complete.startedAt.addingTimeInterval(4)
    try await archive.save([active, complete], revision: 2)
    try await archive.save([], revision: 1)
    let restored = try await archive.load()
    XCTAssertEqual(restored.count, 2)
    XCTAssertEqual(restored[0].outcome, .interrupted)
    XCTAssertNotNil(restored[0].endedAt)
    XCTAssertEqual(restored[1], complete)
  }
  func testRetentionDeletionAndClearKeepActiveRun() {
    let store = RunStore(inMemory: true)
    for index in 0..<105 {
      var record = RunRecord(command: "run \(index)")
      record.startedAt = Date(timeIntervalSince1970: Double(index))
      record.outcome = .completed
      store.upsert(record)
    }
    XCTAssertEqual(store.records.count, 100)
    XCTAssertEqual(store.records.last?.command, "run 5")
    let active = RunRecord(command: "active")
    store.upsert(active)
    store.delete(active.id)
    XCTAssertTrue(store.records.contains { $0.id == active.id })
    store.clear()
    XCTAssertEqual(store.records.map(\.id), [active.id])
  }
  func testStoreRoundTripAndCorruptArchiveIsNotOverwritten() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let url = folder.appendingPathComponent("runs.json")
    let store = RunStore(url: url)
    await store.load()
    var record = RunRecord(command: "scroll down")
    record.outcome = .completed
    record.endedAt = .now
    store.upsert(record)
    await store.flush()
    let restored = RunStore(url: url)
    await restored.load()
    XCTAssertEqual(restored.records, [record])
    await restored.flush()
    let corrupt = Data("unreadable".utf8)
    try corrupt.write(to: url)
    let broken = RunStore(url: url)
    await broken.load()
    broken.upsert(record)
    await broken.flush()
    XCTAssertNotNil(broken.errorMessage)
    XCTAssertEqual(try Data(contentsOf: url), corrupt)
    broken.clear()
    await broken.flush()
    XCTAssertNil(broken.errorMessage)
  }
  func testCostAndIncompleteUsageUseSavedPricing() throws {
    var record = RunRecord(command: "example", pricing: .init(inputPerMillion: 2, outputPerMillion: 4))
    record.requests = [.init(latencyMilliseconds: 100, inputTokens: 1_000, outputTokens: 200), .init(latencyMilliseconds: 300)]
    let summary = UsageSummary(records: [record])
    XCTAssertEqual(summary.estimatedCost, 0.0028, accuracy: 0.0000001)
    XCTAssertTrue(summary.incomplete)
    XCTAssertEqual(summary.requests, 2)
    XCTAssertEqual(summary.inputTokens, 1000)
    XCTAssertEqual(summary.averageLatency, 200)
    XCTAssertEqual(try JSONDecoder().decode(RunRecord.self, from: JSONEncoder().encode(record)), record)
    XCTAssertNil(TokenPricing().estimate(.init(latencyMilliseconds: 0, inputTokens: -1, outputTokens: 0)))
  }
}
