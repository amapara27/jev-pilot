// Exercises cancellation, confirmation, speech boundaries, and local history without desktop effects.
import XCTest
import AVFoundation
@testable import JevPilotCore

@MainActor
private final class FakePerception: DesktopPerceiving {
  var state = DesktopState(activeApplication: .init(name: "Editor", bundleIdentifier: "test.editor", processIdentifier: 123), isAccessibilityTrusted: true)
  var snapshotCalls = 0
  func requestAccessibilityPermission(prompt: Bool) -> Bool { true }
  func snapshot(recentActions: [ActionRecord]) throws -> DesktopState {
    snapshotCalls += 1
    return state
  }
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
  var preparations: [SpeechRecognitionPreset] = []
  var presets: [SpeechRecognitionPreset] = []
  var starts = 0
  var stops = 0
  func prepare(preset: SpeechRecognitionPreset) async { preparations.append(preset) }
  func start(preset: SpeechRecognitionPreset) async {
    starts += 1
    presets.append(preset)
    onEvent?(.ready)
  }
  func stop() { stops += 1 }
  func emit(_ text: String, final: Bool = false) { onEvent?(.transcript(text, isFinal: final)) }
}

private actor FakeStreamingSpeechEngine: StreamingSpeechEngine {
  var prepared: [SpeechRecognitionPreset] = []
  var processedSamples: [Float] = []
  var partial: (@Sendable (String) -> Void)?
  var eou: (@Sendable (String) -> Void)?
  func prepare(
    preset: SpeechRecognitionPreset,
    progress: @escaping @Sendable (Double) -> Void
  ) async throws {
    prepared.append(preset)
    progress(0.5)
    progress(1)
  }
  func configure(
    partial: @escaping @Sendable (String) -> Void,
    eou: @escaping @Sendable (String) -> Void
  ) async throws {
    self.partial = partial
    self.eou = eou
  }
  func process(_ buffer: AVAudioPCMBuffer) async throws {
    processedSamples.append(buffer.floatChannelData?[0][0] ?? -1)
  }
  func cancel() async {}
  func emitPartial(_ text: String) { partial?(text) }
  func emitFinal(_ text: String) { eou?(text) }
  func snapshot() -> ([SpeechRecognitionPreset], [Float]) { (prepared, processedSamples) }
}

@MainActor
private final class FakeAudioSource: SpeechAudioSource {
  var permission = true
  var starts = 0
  private var handler: (@Sendable (AVAudioPCMBuffer) -> Void)?
  func requestPermission() async -> Bool { permission }
  func start(onBuffer: @escaping @Sendable (AVAudioPCMBuffer) -> Void) throws {
    starts += 1
    handler = onBuffer
  }
  func stop() { handler = nil }
  func emit(_ sample: Float) {
    let format = AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1)!
    let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1)!
    buffer.frameLength = 1
    buffer.floatChannelData?[0][0] = sample
    handler?(buffer)
  }
}

private final class TapSampleProbe: @unchecked Sendable {
  private let lock = NSLock()
  private var samples: [Float] = []
  func append(_ sample: Float) { lock.withLock { samples.append(sample) } }
  func snapshot() -> [Float] { lock.withLock { samples } }
}

private final class TapBlockBox: @unchecked Sendable {
  let block: AVAudioNodeTapBlock
  init(_ block: @escaping AVAudioNodeTapBlock) { self.block = block }
}

@MainActor
private final class FakeProbe: GoalProbing {
  var goals: [String] = []
  var continuation: CheckedContinuation<Void, Never>?
  var delayed = false
  var error: Error?
  func release() { continuation?.resume(); continuation = nil }
  func probe(goal: String) async throws -> JevGoalProbeResult {
    goals.append(goal)
    if delayed { await withCheckedContinuation { continuation = $0 } }
    if let error { throw error }
    let state = DesktopState(activeApplication: .init(name: "Editor"), isAccessibilityTrusted: true)
    let candidate = ActionCandidate(id: "STOP", action: .stop(reason: "done"), criterion: "Stop")
    let decision = ActionDecision(candidate: candidate, confidence: 1, probabilities: [candidate.id: 1], model: "fake", latencyMilliseconds: 7)
    return JevGoalProbeResult(goal: goal, desktopState: state, candidates: [candidate], decision: decision)
  }
}

/// Suspends startup until a test explicitly marks this fake storage item as loaded.
@MainActor
private final class ControlledStorageLoader {
  private(set) var started = false
  private(set) var finished = false
  private var continuation: CheckedContinuation<Void, Never>?

  func load() async {
    started = true
    await withCheckedContinuation { continuation = $0 }
    finished = true
  }

  func finish() {
    continuation?.resume()
    continuation = nil
  }
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

  func testFinalCommitsExactlyOnceAndPreservesGoal() async {
    let probe = FakeProbe()
    let speech = FakeSpeech()
    let session = SessionCoordinator(probe: probe, speech: speech, defaults: nil)
    session.startListening()
    await eventually { speech.starts == 1 }
    speech.emit("  switch to Safari  ")
    speech.emit("  switch to Safari  ", final: true)
    speech.emit("late duplicate", final: true)
    await eventually { session.state == .complete }
    XCTAssertEqual(probe.goals, ["switch to Safari"])
    XCTAssertEqual(session.transcript, "switch to Safari")
  }

  func testFluidAudioAdapterPreparesBeforeCaptureAndProcessesBuffersInOrder() async {
    let engine = FakeStreamingSpeechEngine()
    let audio = FakeAudioSource()
    let recognizer = FluidAudioSpeechRecognizer(engine: engine, audioSource: audio)
    var events: [String] = []
    recognizer.onEvent = { event in
      switch event {
      case .transcript(let text, let final): events.append(final ? "final:\(text)" : "partial:\(text)")
      case .ready: events.append("ready")
      case .preparing: break
      case .failed(let message): events.append("failed:\(message)")
      }
    }
    await recognizer.start(preset: .fast160)
    XCTAssertEqual(audio.starts, 1)
    audio.emit(1)
    audio.emit(2)
    audio.emit(3)
    for _ in 0..<100 {
      if await engine.snapshot().1.count == 3 { break }
      try? await Task.sleep(for: .milliseconds(2))
    }
    let snapshot = await engine.snapshot()
    XCTAssertEqual(snapshot.0, [.fast160])
    XCTAssertEqual(snapshot.1, [1, 2, 3])
    await engine.emitPartial("hello")
    await engine.emitFinal("hello world")
    await Task.yield()
    XCTAssertTrue(events.contains("ready"))
    XCTAssertTrue(events.contains("partial:hello"))
    XCTAssertTrue(events.contains("final:hello world"))
    recognizer.stop()
  }

  func testAudioTapHandlerRunsOutsideMainActor() async {
    let probe = TapSampleProbe()
    let tap = TapBlockBox(makeAudioTapHandler { buffer in
      probe.append(buffer.floatChannelData?[0][0] ?? -1)
    })
    let format = AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1)!
    let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1)!
    buffer.frameLength = 1
    buffer.floatChannelData?[0][0] = 42
    DispatchQueue.global().async {
      tap.block(buffer, AVAudioTime(sampleTime: 0, atRate: 16_000))
    }
    await eventually { probe.snapshot() == [42] }
  }

  func testPartialNeverSubmitsAndStopDiscardsIt() async {
    let probe = FakeProbe()
    let speech = FakeSpeech()
    let session = SessionCoordinator(probe: probe, speech: speech, defaults: nil)
    session.startListening()
    speech.emit("switch to Safari")
    try? await Task.sleep(for: .milliseconds(30))
    XCTAssertTrue(probe.goals.isEmpty)
    session.stop()
    XCTAssertEqual(session.transcript, "")
  }

  func testStopDiscardsPartialAndPreviousCaptureCallbacks() async {
    let probe = FakeProbe()
    let speech = FakeSpeech()
    let session = SessionCoordinator(probe: probe, speech: speech, defaults: nil)
    session.startListening()
    await eventually { session.state == .listening }
    let oldCallback = speech.onEvent
    speech.emit("partial")
    session.stop()
    session.startListening()
    oldCallback?(.transcript("late final", isFinal: true))
    try? await Task.sleep(for: .milliseconds(30))
    XCTAssertTrue(probe.goals.isEmpty)
    XCTAssertEqual(session.state, .listening)
    session.stop()
  }

  func testContinuousResumesOnlyAfterSuccess() async {
    let probe = FakeProbe()
    let speech = FakeSpeech()
    let session = SessionCoordinator(probe: probe, speech: speech, defaults: nil)
    session.mode = .continuous
    session.startListening()
    await eventually { speech.starts == 1 }
    speech.emit("switch to Safari", final: true)
    await eventually { speech.starts == 2 }
    XCTAssertEqual(session.state, .listening)
    session.stop()
    XCTAssertEqual(session.state, .stopped)
  }

  func testTypedGoalIsPassedUnchangedAndCreatesNoRunRecord() async {
    let probe = FakeProbe()
    let speech = FakeSpeech()
    let session = SessionCoordinator(probe: probe, speech: speech, defaults: nil)
    session.runTyped("Preserve THIS punctuation!")
    await eventually { session.state == .complete }
    XCTAssertEqual(probe.goals, ["Preserve THIS punctuation!"])
    XCTAssertEqual(session.transcript, "Preserve THIS punctuation!")
  }

  func testPrepareUsesSelectedPresetAndSurfacesProgress() async {
    let probe = FakeProbe()
    let speech = FakeSpeech()
    let session = SessionCoordinator(probe: probe, speech: speech, defaults: nil)
    session.speechPreset = .slow1280
    session.prepareSpeechModel()
    await eventually { speech.preparations == [.slow1280] }
    speech.onEvent?(.preparing(progress: 0.5))
    XCTAssertEqual(session.state, .preparingModel(progress: 0.5))
    speech.onEvent?(.ready)
    XCTAssertEqual(session.state, .complete)
  }

  func testPreparationFailureAndCancelledLoadCannotOverwriteState() async {
    let speech = FakeSpeech()
    let session = SessionCoordinator(probe: FakeProbe(), speech: speech, defaults: nil)
    session.prepareSpeechModel()
    let cancelledCallback = speech.onEvent
    session.stop()
    cancelledCallback?(.preparing(progress: 0.9))
    cancelledCallback?(.ready)
    XCTAssertEqual(session.state, .stopped)

    session.prepareSpeechModel()
    speech.onEvent?(.failed("download failed"))
    XCTAssertEqual(session.state, .error("download failed"))
  }

  func testPresetChangeAppliesToNextCaptureAndRejectsPriorFinal() async {
    let speech = FakeSpeech()
    let probe = FakeProbe()
    let session = SessionCoordinator(probe: probe, speech: speech, defaults: nil)
    session.speechPreset = .fast160
    session.startListening()
    await eventually { session.state == .listening }
    let fastCallback = speech.onEvent
    session.stop()
    session.speechPreset = .slow1280
    session.startListening()
    await eventually { speech.starts == 2 }
    fastCallback?(.transcript("stale", isFinal: true))
    XCTAssertEqual(speech.presets, [.fast160, .slow1280])
    XCTAssertTrue(probe.goals.isEmpty)
    session.stop()
  }

  func testProbeFailureKeepsFinalTranscriptVisible() async {
    let probe = FakeProbe()
    probe.error = CocoaError(.fileNoSuchFile)
    let session = SessionCoordinator(probe: probe, speech: FakeSpeech(), defaults: nil)
    session.runTyped("inspect this")
    await eventually { if case .error = session.state { true } else { false } }
    XCTAssertEqual(session.transcript, "inspect this")
    XCTAssertNil(session.probeResult)
  }

  func testStopDuringDelayedProbeRejectsLateResult() async {
    let probe = FakeProbe()
    probe.delayed = true
    let session = SessionCoordinator(probe: probe, speech: FakeSpeech(), defaults: nil)
    session.runTyped("old goal")
    await eventually { probe.goals == ["old goal"] }
    session.stop()
    session.runTyped("new goal")
    probe.delayed = false
    probe.release()
    await eventually { probe.goals.count == 2 }
    await eventually { session.state == .complete }
    XCTAssertEqual(session.probeResult?.goal, "new goal")
  }

  func testJevGoalProbeCapturesAndDecidesExactlyOnce() async throws {
    let perception = FakePerception()
    let engine = FakeEngine(.stop)
    var restorations = 0
    let probe = JevGoalProbe(
      perception: perception,
      generator: ValidActionGenerator(supportedApplications: []),
      decisionEngine: engine,
      prepareTarget: { restorations += 1 }
    )
    let result = try await probe.probe(goal: "do nothing")
    XCTAssertEqual(result.goal, "do nothing")
    XCTAssertEqual(restorations, 1)
    XCTAssertEqual(perception.snapshotCalls, 1)
    let engineCalls = await engine.calls
    XCTAssertEqual(engineCalls, 1)
    XCTAssertEqual(result.decision.candidate.action, .stop(reason: "Goal complete or no safe valid action remains"))
    XCTAssertTrue(result.requestMetric?.isComplete == true)
  }
}

@MainActor
final class RunStoreTests: XCTestCase {
  private func eventually(
    _ predicate: @MainActor () -> Bool,
    file: StaticString = #filePath,
    line: UInt = #line
  ) async {
    for _ in 0..<300 {
      if predicate() { return }
      try? await Task.sleep(for: .milliseconds(5))
    }
    XCTFail("Condition was not reached", file: file, line: line)
  }

  func testStartupWaitsForEveryStorageItemAndLoadsThemTogether() async {
    let first = ControlledStorageLoader()
    let second = ControlledStorageLoader()
    let startup = StorageStartupCoordinator(loaders: [
      { await first.load() },
      { await second.load() },
    ])

    let task = Task { await startup.load() }
    await eventually { first.started && second.started }
    XCTAssertEqual(startup.phase, .loading)

    first.finish()
    await eventually { first.finished }
    XCTAssertEqual(startup.phase, .loading)

    second.finish()
    await task.value
    XCTAssertTrue(second.finished)
    XCTAssertEqual(startup.phase, .ready)
  }

  func testFullHistoryStartupCompletesWithinTwoSeconds() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: folder) }
    let url = folder.appendingPathComponent("runs.json")
    let archive = RunArchive(url: url)
    let records = (0..<100).map { index in
      var record = RunRecord(command: "run \(index)")
      record.startedAt = Date(timeIntervalSince1970: Double(index))
      record.outcome = .completed
      return record
    }
    try await archive.save(records, revision: 1)
    let store = RunStore(url: url)
    let startup = StorageStartupCoordinator(loaders: [{ await store.load() }])

    let clock = ContinuousClock()
    let startedAt = clock.now
    await startup.load()
    let elapsed = startedAt.duration(to: clock.now)

    XCTAssertEqual(store.records.count, 100)
    XCTAssertTrue(startup.isReady)
    XCTAssertLessThan(elapsed, .seconds(2), "Loading the maximum retained history should not delay launch noticeably.")
  }

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
