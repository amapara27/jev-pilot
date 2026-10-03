// Exercises the transcript-to-history pipeline with only speech, desktop and HTTP boundaries simulated.
import XCTest
@testable import JevPilotCore

/// An offline desktop; production parsing, provider decoding, safety and verification stay active.
@MainActor
private final class TestDesktop: DesktopPerceiving, ActionExecuting {
  let apps = [
    ApplicationState(name: "Chat", bundleIdentifier: "test.chat", processIdentifier: 999990),
    ApplicationState(name: "Notes", bundleIdentifier: "com.apple.Notes", processIdentifier: 999991),
    ApplicationState(name: "Finder", bundleIdentifier: "com.apple.finder", processIdentifier: 999992),
    ApplicationState(name: "Terminal", bundleIdentifier: "com.apple.Terminal", processIdentifier: 999993),
  ]
  var state = DesktopState(isAccessibilityTrusted: true)
  var actions: [AutomationAction] = []
  var restorations: [Int32?] = []
  var failExecution = false
  var omitEffect = false
  var bodyID = "body"
  var noteCount = 0
  init() { activate("test.chat") }
  func activate(_ bundle: String) {
    state.activeApplication = apps.first { $0.bundleIdentifier == bundle }
    state.runningApplications = apps
    state.focusedWindowID = "window:\(bundle)"
    state.windows = [.init(id: state.focusedWindowID!, title: "Ready", role: "AXWindow", isFocused: true)]
    state.focusedElementID = bodyID
    state.elements = [.init(id: bodyID, role: "AXTextArea", value: "", isFocused: true,
      textSelection: .init(location: 0, length: 0))]
  }
  func restore(_ pid: Int32?) async throws {
    restorations.append(pid)
    if pid != state.activeApplication?.processIdentifier,
      let app = apps.first(where: { $0.processIdentifier == pid }), let bundle = app.bundleIdentifier { activate(bundle) }
  }
  func requestAccessibilityPermission(prompt: Bool) -> Bool { true }
  func snapshot(recentActions: [ActionRecord]) throws -> DesktopState {
    var result = state
    result.recentActions = recentActions
    return result
  }
  func setBody(_ text: String, selection: TextSelection? = nil, focused: Bool = true, depth: Int = 1) {
    state.focusedElementID = focused ? bodyID : nil
    state.elements = [.init(id: bodyID, role: "AXTextArea", value: text, isFocused: focused, depth: depth,
      textSelection: selection ?? .init(location: text.utf16.count, length: 0))]
  }
  func execute(_ action: AutomationAction) async -> ExecutionResult {
    actions.append(action)
    if failExecution { return .init(succeeded: false, message: "Test native failure") }
    if omitEffect { return .init(succeeded: true, message: "Native accepted but did nothing") }
    switch action {
    case .openApp(let id, _), .focusApp(let id, _): activate(id)
    case .notesCreateNote:
      noteCount += 1
      setBody("")
      state.elements.append(.init(id: "note:\(noteCount)", role: "AXRow", label: "New Note", isSelected: true))
    case .focusElement: setBody(state.elements[0].value ?? "")
    case .typeText(_, let text), .terminalType(let text):
      let before = state.elements.first { $0.id == bodyID }!
      setBody(TextInput.expectedValue(before: before, inserting: text)!)
    case .searchInApp(let query):
      state.focusedElementID = "search"
      state.elements = [.init(id: "search", role: "AXTextField", subrole: "AXSearchField", label: "Search", value: query, isFocused: true)]
    case .finderOpenFolder(let path):
      state.windows = [.init(id: state.focusedWindowID!, title: URL(fileURLWithPath: path).lastPathComponent,
        role: "AXWindow", isFocused: true, url: path)]
    default: break
    }
    return .init(succeeded: true, message: "Simulated native boundary")
  }
}

@MainActor
private final class TestSpeech: SpeechProviding {
  var onEvent: ((SpeechEvent) -> Void)?
  func start(preset: SpeechRecognitionPreset) async { onEvent?(.ready) }
  func finish() async {}
  func stop() {}
  func commit(_ text: String) { onEvent?(.transcript(text, isFinal: true)) }
}

/// An HTTP fixture, not a fake DecisionEngine: real request construction and validation run.
private final class PipelineHTTP: URLProtocol, @unchecked Sendable {
  struct Settings: Sendable {
    var total = 1.0
    var confidence = 1.0
    var forceStop = false
    var extraKey = false
    var wrongWinner = false
    var delay: Duration = .zero
    var hook: (@MainActor @Sendable (String) -> Void)?
  }
  private final class Shared: @unchecked Sendable {
    let lock = NSLock()
    var settings = Settings()
    var goals: [String] = []
    func configure(_ value: Settings) { lock.withLock { settings = value; goals = [] } }
    func record(_ goal: String) -> Settings { lock.withLock { goals.append(goal); return settings } }
    var requestedGoals: [String] { lock.withLock { goals } }
  }
  private static let shared = Shared()
  static func configure(_ settings: Settings = .init()) { shared.configure(settings) }
  static var goals: [String] { shared.requestedGoals }
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    var data = request.httpBody ?? Data()
    if data.isEmpty, let stream = request.httpBodyStream {
      stream.open()
      defer { stream.close() }
      var buffer = [UInt8](repeating: 0, count: 4096)
      while stream.hasBytesAvailable {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count <= 0 { break }
        data.append(contentsOf: buffer.prefix(count))
      }
    }
    let body = try! JSONSerialization.jsonObject(with: data) as! [String: Any]
    let state = body["state"] as! [String: Any]
    let goal = state["goal"] as! String
    let questions = body["questions"] as! [String: [String: Any]]
    let criteria = questions["next_action"]!["criteria"] as! [String: String]
    let settings = Self.shared.record(goal)
    let lower = goal.lowercased()
    let desired: String
    if lower.hasPrefix("create") { desired = "Concrete action: Create a new note" }
    else if lower.hasPrefix("type") || lower.hasPrefix("write") { desired = "Concrete action: Type " }
    else if lower.hasPrefix("locate") || lower.hasPrefix("find") { desired = "Concrete action: Search for" }
    else if lower.hasPrefix("open folder") { desired = "Concrete action: Open folder" }
    else { desired = "Concrete action: Focus " }
    var choice = criteria.first { $0.value.contains(desired) }?.key
      ?? criteria.first { $0.value.contains("Concrete action: Focus ") }?.key
      ?? criteria.first { $0.value.contains("Concrete action: Open ") }?.key
      ?? criteria.first { $0.value.contains("Concrete action: Stop:") }!.key
    if settings.forceStop { choice = criteria.first { $0.value.contains("Concrete action: Stop:") }!.key }
    let winner = choice
    let weight = settings.total > 1.01 ? 0.8 : 0.98
    var probabilities = Dictionary(uniqueKeysWithValues: criteria.keys.map {
      ($0, $0 == winner ? weight * settings.total : (1 - weight) * settings.total / Double(max(1, criteria.count - 1)))
    })
    if criteria.count == 1 { probabilities[winner] = settings.total }
    if settings.extraKey { probabilities["unknown"] = 0 }
    if settings.wrongWinner { choice = criteria.keys.first { $0 != winner }! }
    let response = try! JSONSerialization.data(withJSONObject: ["model": "pipeline-fixture", "usage": ["input_tokens": 10, "output_tokens": 2],
      "answers": ["next_action": ["type": "choice", "choice": choice, "confidence": settings.confidence, "probabilities": probabilities]]])
    Task {
      if let hook = settings.hook { await hook(goal) }
      // Deliberately ignores cancellation so the controller's generation guard is exercised.
      try? await Task.sleep(for: settings.delay)
      client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: response)
      client?.urlProtocolDidFinishLoading(self)
    }
  }
  override func stopLoading() {}
}

@MainActor
final class EndToEndTests: XCTestCase {
  private struct Harness {
    let desktop: TestDesktop
    let speech: TestSpeech
    let controller: AutomationController
    let session: SessionCoordinator
  }
  private func harness() -> Harness {
    let desktop = TestDesktop(), speech = TestSpeech()
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [PipelineHTTP.self]
    let generator = ValidActionGenerator(supportedApplications: ValidActionGenerator.defaultApplications,
      applicationURL: { _ in URL(fileURLWithPath: "/Applications/test.app") })
    let controller = AutomationController(perception: desktop, actionGenerator: generator,
      decisionEngine: JevDecisionEngine(session: URLSession(configuration: config), apiKeyProvider: { "offline-fixture-key" }),
      executor: desktop, completionVerifier: DesktopActionCompletionVerifier(timeout: .milliseconds(100)),
      prepareTarget: { try await desktop.restore($0) })
    let session = SessionCoordinator(controller: controller, speech: speech, defaults: nil,
      intendedTarget: { desktop.state.activeApplication?.processIdentifier })
    return .init(desktop: desktop, speech: speech, controller: controller, session: session)
  }
  private func wait(_ condition: () -> Bool) async throws {
    let deadline = ContinuousClock.now.advanced(by: .seconds(3))
    while !condition(), ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(5)) }
    XCTAssertTrue(condition(), "Pipeline did not reach the expected state")
  }

  func testTranscriptThroughVerifiedTaskAndHistory() async throws {
    var records: [RunRecord] = []
    let cases: [(String, [ActionKind], String)] = [
      ("open Notes create a new note and type Hello from Jev", [.focusApp, .notesCreateNote, .typeText], "Hello from Jev"),
      ("Open Notes. Create a new note. Type Hello.", [.focusApp, .notesCreateNote, .typeText], "Hello."),
      ("type bread and butter in a new note", [.focusApp, .notesCreateNote, .typeText], "bread and butter"),
      ("open Finder and open folder /tmp/Jev-fixture and locate resumes", [.focusApp, .finderOpenFolder, .searchInApp], "resumes"),
      ("open Terminal and type pwd", [.focusApp, .terminalType], "pwd"),
    ]
    for (goal, kinds, payload) in cases {
      PipelineHTTP.configure(.init(total: 1.005))
      let h = harness()
      h.session.startListening()
      try await wait { h.session.captureState == .listening }
      h.speech.commit(goal)
      try await wait { h.controller.currentRun?.outcome != nil }
      try await wait { h.controller.currentRun?.requests.allSatisfy(\.isComplete) == true }
      XCTAssertEqual(h.controller.currentRun?.outcome, .completed, h.controller.status.label)
      XCTAssertEqual(h.desktop.actions.map(\.kind), kinds)
      XCTAssertEqual(h.desktop.state.elements.first(where: \.isFocused)?.value, payload)
      XCTAssertTrue(h.desktop.restorations.isEmpty, "Named commands must not restore Chat")
      XCTAssertEqual(h.session.captureState, .listening)
      XCTAssertEqual(h.controller.store.records.count, 1)
      XCTAssertTrue(h.controller.currentRun!.requests.allSatisfy { $0.distributionNormalized == true && $0.candidateCount != nil })
      XCTAssertEqual(h.controller.latestDecision!.probabilities.values.reduce(0, +), 1, accuracy: 0.000_001)
      records.append(h.controller.currentRun!)
      h.session.stop()
    }
    let output = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent(".build/e2e-offline.json")
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    try encoder.encode(records).write(to: output, options: .atomic)
  }

  func testDriftRecoveryAndUnsafeChangesThroughPipeline() async throws {
    for scenario in ["title", "control", "caret", "app", "window", "stop", "sum", "keys", "winner", "native", "unverified", "sensitive"] {
      let h = harness()
      h.desktop.activate("com.apple.Notes")
      var requests = 0
      var settings = PipelineHTTP.Settings()
      settings.hook = { _ in
        requests += 1
        guard requests == 1 else { return }
        switch scenario {
        case "title":
          h.desktop.state.windows = [.init(id: h.desktop.state.focusedWindowID!, title: "Changed harmless title", role: "AXWindow", isFocused: true)]
          h.desktop.setBody("", depth: 5)
        case "control": h.desktop.bodyID = "replacement"; h.desktop.setBody("")
        case "caret": h.desktop.setBody("User edited")
        case "app": h.desktop.activate("test.chat")
        case "window": h.desktop.state.focusedWindowID = "different-window"
        default: break
        }
      }
      settings.forceStop = scenario == "stop"
      settings.total = scenario == "sum" ? 1.2 : 1
      settings.extraKey = scenario == "keys"
      settings.wrongWinner = scenario == "winner"
      h.desktop.failExecution = scenario == "native"
      h.desktop.omitEffect = scenario == "unverified"
      PipelineHTTP.configure(settings)
      h.session.runTyped(scenario == "sensitive" ? "type password" : "type Hello")
      try await wait { h.controller.currentRun?.outcome != nil }
      if ["title", "control"].contains(scenario) {
        XCTAssertEqual(h.controller.currentRun?.outcome, .completed, scenario)
        XCTAssertEqual(h.desktop.actions.count, 1)
        XCTAssertEqual(PipelineHTTP.goals.count, scenario == "control" ? 2 : 1)
      } else {
        XCTAssertNotEqual(h.controller.currentRun?.outcome, .completed, scenario)
        XCTAssertTrue(h.session.queuePaused)
        XCTAssertEqual(h.desktop.actions.count, ["native", "unverified"].contains(scenario) ? 1 : 0, scenario)
        if scenario == "sensitive" { XCTAssertTrue(PipelineHTTP.goals.isEmpty) }
        if scenario == "sum" {
          try await wait { h.controller.currentRun?.requests.last?.isComplete == true }
          XCTAssertEqual(h.controller.currentRun?.requests.last?.validationFailure, "probability_sum")
          XCTAssertTrue(h.controller.currentRun!.events.last!.detail.contains("1.200000"))
        }
      }
      h.session.stop()
    }
  }

  func testQueueConfirmationCorrectionAndCancellationThroughPipeline() async throws {
    PipelineHTTP.configure(.init(confidence: 0.7))
    let h = harness()
    h.session.startListening()
    try await wait { h.session.captureState == .listening }
    h.speech.commit("create a new note and type First")
    try await wait { h.controller.pendingConfirmation != nil }
    h.speech.commit("type Second")
    XCTAssertEqual(h.session.queuedGoals.count, 1)
    XCTAssertEqual(h.session.captureState, .listening)
    let queueDeadline = ContinuousClock.now.advanced(by: .seconds(3))
    while h.session.activeGoal != nil || !h.session.queuedGoals.isEmpty, ContinuousClock.now < queueDeadline {
      if h.controller.pendingConfirmation != nil { h.session.confirmPendingAction() }
      try await Task.sleep(for: .milliseconds(5))
      if h.session.queuePaused { break }
    }
    XCTAssertEqual(h.controller.store.records.count, 2)
    XCTAssertEqual(h.desktop.state.elements.first?.value, "FirstSecond")
    XCTAssertFalse(h.session.queuePaused)
    // A correction replaces pending work, pauses, and never inherits approval.
    h.speech.commit("type Third")
    try await wait { h.controller.pendingConfirmation != nil }
    h.speech.commit("actually type Corrected")
    XCTAssertTrue(h.session.queuePaused)
    XCTAssertEqual(h.session.queuedGoals.last?.text, "type Corrected")
    h.session.resumeQueue()
    try await wait { h.controller.pendingConfirmation != nil }
    h.session.rejectPendingAction()
    XCTAssertTrue(h.session.queuePaused)
    h.session.stop()
    XCTAssertTrue(h.session.queuedGoals.isEmpty)
    PipelineHTTP.configure(.init(confidence: 0.7))
    h.session.runTyped("type Capacity")
    try await wait { h.controller.pendingConfirmation != nil }
    for index in 0..<6 { h.session.runTyped("type Pending \(index)") }
    XCTAssertEqual(h.session.queuedGoals.count, 5)
    XCTAssertTrue(h.session.queuePaused)
    h.session.stop()
    XCTAssertTrue(h.session.queuedGoals.isEmpty)
    // Failure pauses pending work; Resume resolves fresh targets at dequeue.
    PipelineHTTP.configure(.init(forceStop: true, delay: .milliseconds(30)))
    let resumed = harness()
    resumed.desktop.activate("com.apple.Notes")
    resumed.session.runTyped("type blocked")
    resumed.session.runTyped("type recovered")
    try await wait { resumed.session.queuePaused }
    XCTAssertEqual(resumed.session.queuedGoals.count, 1)
    PipelineHTTP.configure()
    resumed.session.resumeQueue()
    try await wait { resumed.controller.currentRun?.command == "type recovered" && resumed.controller.currentRun?.outcome == .completed }
    XCTAssertEqual(resumed.desktop.state.elements.first?.value, "recovered")
    XCTAssertEqual(resumed.controller.store.records.count, 2)
    // A delayed HTTP response arriving after Stop must not type or overwrite a new run.
    PipelineHTTP.configure(.init(delay: .milliseconds(100)))
    let late = harness()
    late.desktop.activate("com.apple.Notes")
    late.session.runTyped("type old")
    try await wait { !PipelineHTTP.goals.isEmpty }
    late.session.stop()
    PipelineHTTP.configure()
    late.session.runTyped("type fresh")
    try await wait { late.controller.currentRun?.outcome == .completed }
    try await Task.sleep(for: .milliseconds(150))
    XCTAssertEqual(late.desktop.state.elements.first?.value, "fresh")
    XCTAssertEqual(late.desktop.actions.count, 1)
  }
}
