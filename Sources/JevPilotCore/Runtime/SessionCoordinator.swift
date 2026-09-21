// Shares listening and execution state across the window, menu bar, and transcript panel.
import Combine
import Foundation

public enum ListeningMode: String, CaseIterable, Identifiable, Sendable {
  case single, continuous
  public var id: Self { self }
  public var title: String { self == .single ? "Single command" : "Continuous" }
}

/// Coordinates voice submission without changing the core safety policy.
@MainActor
public final class SessionCoordinator: ObservableObject {
  public enum State: Equatable {
    case stopped, listening, executing, awaitingConfirmation, error(String)
    public var label: String {
      switch self {
      case .stopped: "Ready when you are"
      case .listening: "Listening"
      case .executing: "Working"
      case .awaitingConfirmation: "Needs your confirmation"
      case .error(let message): message
      }
    }
    public var isActive: Bool {
      switch self { case .listening, .executing, .awaitingConfirmation: true; default: false }
    }
  }
  @Published public private(set) var state: State = .stopped
  @Published public private(set) var transcript = ""
  @Published public var mode: ListeningMode {
    didSet { defaults?.set(mode.rawValue, forKey: "listeningMode") }
  }
  @Published public var showTranscript: Bool {
    didSet { defaults?.set(showTranscript, forKey: "showTranscript") }
  }
  public let controller: AutomationController
  private let speech: any SpeechProviding
  private let defaults: UserDefaults?
  private let prepareTarget: @MainActor (Int32?) async throws -> Void
  private let readiness: @MainActor () -> String?
  private let silenceMilliseconds: Int
  private var generation = UUID()
  private var committed = false
  private var voiceSession = false
  private var silenceTask: Task<Void, Never>?
  private var workTask: Task<Void, Never>?
  private var subscription: AnyCancellable?

  public init(
    controller: AutomationController, speech: any SpeechProviding, defaults: UserDefaults? = .standard,
    silenceMilliseconds: Int = 800,
    readiness: @escaping @MainActor () -> String? = { nil },
    prepareTarget: @escaping @MainActor (Int32?) async throws -> Void = { _ in }
  ) {
    self.controller = controller
    self.speech = speech
    self.defaults = defaults
    self.silenceMilliseconds = silenceMilliseconds
    self.readiness = readiness
    self.prepareTarget = prepareTarget
    mode = ListeningMode(rawValue: defaults?.string(forKey: "listeningMode") ?? "") ?? .single
    showTranscript = defaults?.bool(forKey: "showTranscript") ?? false
    subscription = controller.$status.sink { [weak self] status in self?.controllerChanged(status) }
  }

  public func startListening() {
    guard !state.isActive else { return }
    guard controller.store.isLoaded else { state = .error("Run history is still loading."); return }
    if let error = readiness() { state = .error(error); return }
    voiceSession = true
    beginCapture()
  }
  public func runTyped(_ command: String) {
    guard !state.isActive, !command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
    if let error = readiness() { state = .error(error); return }
    guard controller.store.isLoaded else { state = .error("Run history is still loading."); return }
    voiceSession = false
    generation = UUID()
    transcript = command
    committed = false
    submit(command, generation: generation)
  }
  public func stop() {
    generation = UUID()
    voiceSession = false
    silenceTask?.cancel()
    workTask?.cancel()
    speech.stop()
    controller.cancel()
    transcript = ""
    state = .stopped
  }
  public func reject() {
    voiceSession = false
    controller.rejectPendingAction()
    state = .stopped
  }
  public func confirm() {
    guard state == .awaitingConfirmation else { return }
    state = .executing
    let id = generation
    let pid = controller.latestState?.activeApplication?.processIdentifier
    workTask = Task { [weak self] in
      guard let self else { return }
      do {
        try await self.prepareTarget(pid)
        guard self.generation == id, !Task.isCancelled else { return }
        self.controller.confirmPendingAction()
      } catch {
        guard self.generation == id else { return }
        self.stop()
        self.state = .error(error.localizedDescription)
      }
    }
  }

  private func beginCapture() {
    generation = UUID()
    let id = generation
    committed = false
    transcript = ""
    state = .listening
    speech.onEvent = { [weak self] event in self?.receive(event, generation: id) }
    workTask = Task { [weak self] in
      guard let self, self.generation == id, !Task.isCancelled else { return }
      await self.speech.start()
    }
  }
  private func receive(_ event: SpeechEvent, generation id: UUID) {
    guard id == generation, state == .listening, !committed else { return }
    switch event {
    case .failed(let message):
      stop()
      state = .error(message)
    case .transcript(let text, let isFinal):
      let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
      let changed = text != transcript
      transcript = text
      if isFinal, !text.isEmpty { submit(text, generation: id); return }
      if isFinal, text.isEmpty {
        stop()
        return
      }
      guard changed else { return }
      silenceTask?.cancel()
      guard !text.isEmpty else { return }
      silenceTask = Task { [weak self] in
        guard let self else { return }
        do { try await Task.sleep(for: .milliseconds(self.silenceMilliseconds)) } catch { return }
        self.submit(text, generation: id)
      }
    }
  }
  private func submit(_ command: String, generation id: UUID) {
    guard generation == id, !committed else { return }
    committed = true
    silenceTask?.cancel()
    speech.stop()
    state = .executing
    workTask = Task { [weak self] in
      guard let self else { return }
      do {
        try await self.prepareTarget(nil)
        guard self.generation == id, !Task.isCancelled else { return }
        self.controller.run(goal: command)
      } catch {
        guard self.generation == id else { return }
        self.stop()
        self.state = .error(error.localizedDescription)
      }
    }
  }
  private func controllerChanged(_ status: AutomationController.Status) {
    switch status {
    case .running: state = .executing
    case .awaitingConfirmation: state = .awaitingConfirmation
    case .completed:
      if voiceSession && mode == .continuous { beginCapture() }
      else { state = .stopped; voiceSession = false }
    case .failed(let message), .blocked(let message):
      voiceSession = false
      speech.stop()
      state = .error(message)
    case .stopped, .rejected: voiceSession = false; state = .stopped
    case .idle: break
    }
  }
}
