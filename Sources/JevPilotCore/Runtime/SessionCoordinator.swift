// Shares local transcription and desktop automation state across every app surface.
import AppKit
import Combine
import Foundation

/// Coordinates capture and hands one final goal to the bounded automation controller.
@MainActor
public final class SessionCoordinator: ObservableObject {
  public struct QueuedGoal: Identifiable, Equatable {
    public let id: UUID
    public let text: String
    public let queuedAt: Date
    public let intendedProcessIdentifier: Int32?
    public let speechMilliseconds: Int?
    public init(id: UUID = UUID(), text: String, queuedAt: Date = .now,
      intendedProcessIdentifier: Int32? = nil, speechMilliseconds: Int? = nil) {
      self.id = id; self.text = text; self.queuedAt = queuedAt
      self.intendedProcessIdentifier = intendedProcessIdentifier
      self.speechMilliseconds = speechMilliseconds
    }
  }

  public enum CaptureState: Equatable {
    case stopped, preparing(Double?), listening, failed(String)
  }

  public enum State: Equatable {
    case stopped
    case preparingModel(progress: Double?)
    case listening
    case askingJev
    case running(step: Int)
    case awaitingConfirmation
    case awaitingAppChoice
    case complete
    case rejected
    case blocked(String)
    case error(String)

    public var label: String {
      switch self {
      case .stopped: "Ready when you are"
      case .preparingModel(let progress):
        progress.map { "Preparing model · \(Int($0 * 100))%" } ?? "Preparing model"
      case .listening: "Listening"
      case .askingJev: "Asking Jev"
      case .running(let step): "Running step \(step)"
      case .awaitingConfirmation: "Needs confirmation"
      case .awaitingAppChoice: "Choose an app"
      case .complete: "Complete"
      case .rejected: "Rejected"
      case .blocked(let message): message
      case .error(let message): message
      }
    }
    public var isActive: Bool {
      switch self {
      case .preparingModel, .listening, .askingJev, .running, .awaitingConfirmation, .awaitingAppChoice: true
      case .stopped, .complete, .rejected, .blocked, .error: false
      }
    }
  }

  @Published public private(set) var state: State = .stopped
  @Published public private(set) var captureState: CaptureState = .stopped
  @Published public private(set) var queuedGoals: [QueuedGoal] = []
  @Published public private(set) var activeGoal: QueuedGoal?
  @Published public private(set) var queuePaused = false
  @Published public private(set) var pauseReason: String?
  @Published public private(set) var transcript = ""
  @Published public var showTranscript: Bool {
    didSet { defaults?.set(showTranscript, forKey: "showTranscript") }
  }
  @Published public var speechPreset: SpeechRecognitionPreset {
    didSet { defaults?.set(speechPreset.rawValue, forKey: "speechPreset") }
  }

  private let speech: any SpeechProviding
  public let controller: AutomationController
  private let defaults: UserDefaults?
  private var controllerStatusSubscription: AnyCancellable?
  private var generation = UUID()
  private var workTask: Task<Void, Never>?
  private var speechSegmentStartedAt: Date?
  private let queueLimit = 5

  public init(
    controller: AutomationController,
    speech: any SpeechProviding,
    defaults: UserDefaults? = .standard
  ) {
    self.controller = controller
    self.speech = speech
    self.defaults = defaults
    showTranscript = defaults?.bool(forKey: "showTranscript") ?? false
    speechPreset = SpeechRecognitionPreset(
      rawValue: defaults?.string(forKey: "speechPreset") ?? ""
    ) ?? .balanced320
    controllerStatusSubscription = controller.$status.dropFirst().sink { [weak self] status in
      self?.receiveControllerStatus(status)
    }
  }

  /// Starts a continuous capture; each EOU adds one complete command to the queue.
  public func startListening() {
    switch captureState {
    case .stopped, .failed: break
    case .preparing, .listening: return
    }
    beginCapture()
  }

  /// Flushes the current microphone stream and submits its best final transcript.
  public func finishListening() {
    guard captureState == .listening else { return }
    workTask = Task { [weak self] in
      guard let self else { return }
      await self.speech.finish()
    }
  }

  /// Typed text bypasses STT but uses the same bounded automation path.
  public func runTyped(_ command: String) {
    enqueue(command)
  }

  /// Manual Stop cancels model, audio, and Jev work and never commits partial speech.
  public func stop() {
    queuedGoals.removeAll()
    activeGoal = nil
    queuePaused = false
    pauseReason = nil
    if controller.status.isActive { controller.cancel() }
    invalidate(clearTranscript: captureState == .listening || isPreparing)
    captureState = .stopped
    state = .stopped
  }

  /// A paused queue resumes only after the user checks the current desktop.
  public func resumeQueue() {
    guard queuePaused else { return }
    queuePaused = false
    pauseReason = nil
    startNextIfIdle()
  }

  /// Confirmation is explicit; the controller restores and rechecks the desktop target.
  public func confirmPendingAction() { controller.confirmPendingAction() }
  public func rejectPendingAction() { controller.rejectPendingAction() }
  public func chooseApplication(_ bundleIdentifier: String) { controller.chooseApplication(bundleIdentifier: bundleIdentifier) }

  /// An edited shell command is a new goal and must receive a new Jev decision.
  public func revisePendingTerminalCommand(_ command: String) {
    guard let pending = controller.pendingConfirmation else { return }
    guard case .terminalRun = pending.decision.candidate.action else { return }
    let value = command.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !value.isEmpty else { return }
    if queuedGoals.count >= queueLimit { queuedGoals.removeLast() }
    queuedGoals.insert(QueuedGoal(text: "run command \(value)"), at: 0)
    controller.cancel()
    queuePaused = false
    pauseReason = nil
    startNextIfIdle()
  }

  private var isPreparing: Bool {
    if case .preparing = captureState { return true }
    return false
  }

  private func invalidate(clearTranscript: Bool) {
    generation = UUID()
    workTask?.cancel()
    workTask = nil
    speech.stop()
    if clearTranscript { transcript = "" }
    speechSegmentStartedAt = nil
  }

  private func beginCapture() {
    invalidate(clearTranscript: true)
    let id = generation
    captureState = .preparing(nil)
    state = .preparingModel(progress: nil)
    speech.onEvent = { [weak self] event in self?.receive(event, generation: id) }
    workTask = Task { [weak self] in
      guard let self else { return }
      await self.speech.start(preset: self.speechPreset)
    }
  }

  private func receive(
    _ event: SpeechEvent,
    generation id: UUID
  ) {
    guard id == generation else { return }
    switch event {
    case .preparing(let progress):
      captureState = .preparing(progress)
      state = .preparingModel(progress: progress)
    case .ready:
      captureState = .listening
      state = .listening
    case .failed(let message):
      invalidate(clearTranscript: false)
      captureState = .failed(message)
      state = .error(message)
    case .transcript(let text, let isFinal):
      guard captureState == .listening else { return }
      transcript = text
      if !isFinal {
        if !text.isEmpty && speechSegmentStartedAt == nil { speechSegmentStartedAt = .now }
        return
      }
      let speechMilliseconds = speechSegmentStartedAt.map { max(0, Int(Date().timeIntervalSince($0) * 1_000)) }
      speechSegmentStartedAt = nil
      let goal = text.trimmingCharacters(in: .whitespacesAndNewlines)
      guard !goal.isEmpty else { return }
      // Preserve the model's words verbatim, removing only surrounding whitespace.
      transcript = goal
      enqueue(goal, speechMilliseconds: speechMilliseconds)
    }
  }

  private func enqueue(_ raw: String, speechMilliseconds: Int? = nil) {
    let goal = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !goal.isEmpty else { return }
    transcript = goal
    if let correction = correctionPayload(goal), activeGoal != nil || !queuedGoals.isEmpty {
      let replaced = queuedGoals.isEmpty ? activeGoal : queuedGoals.removeLast()
      queuedGoals.append(QueuedGoal(text: correction,
        intendedProcessIdentifier: replaced?.intendedProcessIdentifier,
        speechMilliseconds: speechMilliseconds))
      queuePaused = true
      pauseReason = "Correction received. Review the updated command, then resume."
      controller.requestPauseAfterCurrentEffect()
      return
    }
    guard queuedGoals.count < queueLimit else {
      queuePaused = true
      pauseReason = "Queue full. Resume or clear pending commands before speaking more."
      return
    }
    let frontmost = NSWorkspace.shared.frontmostApplication?.processIdentifier
    let target = frontmost != ProcessInfo.processInfo.processIdentifier ? frontmost : nil
    queuedGoals.append(QueuedGoal(text: goal, intendedProcessIdentifier: target,
      speechMilliseconds: speechMilliseconds))
    startNextIfIdle()
  }

  private func correctionPayload(_ text: String) -> String? {
    for prefix in ["actually ", "no, instead ", "instead "] {
      if text.lowercased().hasPrefix(prefix) {
        let result = String(text.dropFirst(prefix.count)).trimmingCharacters(in: .whitespacesAndNewlines)
        return result.isEmpty ? nil : result
      }
    }
    return nil
  }

  private func startNextIfIdle() {
    guard !queuePaused, activeGoal == nil, !queuedGoals.isEmpty, !controller.status.isActive else { return }
    let next = queuedGoals.removeFirst()
    activeGoal = next
    state = captureState == .listening ? .listening : .askingJev
    controller.run(goal: next.text, targetProcessIdentifier: next.intendedProcessIdentifier,
      queuedAt: next.queuedAt, speechMilliseconds: next.speechMilliseconds)
  }

  private func receiveControllerStatus(_ status: AutomationController.Status) {
    switch status {
    case .idle: break
    case .running(let step): if captureState != .listening { state = .running(step: step) }
    case .awaitingConfirmation: if captureState != .listening { state = .awaitingConfirmation }
    case .awaitingAppChoice: if captureState != .listening { state = .awaitingAppChoice }
    case .completed:
      activeGoal = nil
      state = captureState == .listening ? .listening : .complete
      Task { @MainActor [weak self] in self?.startNextIfIdle() }
    case .stopped, .rejected, .blocked, .failed:
      guard activeGoal != nil else { return }
      activeGoal = nil
      queuePaused = true
      if pauseReason == nil { pauseReason = status.label }
      if captureState != .listening {
        switch status {
        case .stopped: state = .stopped
        case .rejected: state = .rejected
        case .blocked(let reason): state = .blocked(reason)
        case .failed(let reason): state = .error(reason)
        default: break
        }
      }
    }
  }
}
