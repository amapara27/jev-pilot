// Provides macOS permissions, foreground targeting, and shared application services.
import AppKit
import ApplicationServices
import AVFoundation
import Combine
import JevPilotCore
import SwiftUI

/// Reads permissions and API-key existence without fetching confidential key bytes.
@MainActor
final class Readiness: ObservableObject {
  @Published var accessibility = false
  @Published var microphone = AVAuthorizationStatus.notDetermined
  @Published var hasKey = false
  @Published var hasStoredKey = false
  @Published var hasDevelopmentKey = false
  @Published var keyError: String?

  private let storedKeyStatus: () throws -> Bool
  private let developmentKeyStatus: () throws -> Bool

  init(
    storedKeyStatus: @escaping () throws -> Bool = {
      try KeychainAPIKeyStore().containsKey()
    },
    developmentKeyStatus: @escaping () throws -> Bool = {
      try DevelopmentAPIKey.loadOverride() != nil
    }
  ) {
    self.storedKeyStatus = storedKeyStatus
    self.developmentKeyStatus = developmentKeyStatus
  }

  func refresh() {
    accessibility = AXIsProcessTrusted()
    microphone = AVCaptureDevice.authorizationStatus(for: .audio)
    do {
      hasDevelopmentKey = try developmentKeyStatus()
      keyError = nil
    } catch {
      hasDevelopmentKey = false
      hasStoredKey = false
      hasKey = false
      keyError = error.localizedDescription
      return
    }
    if hasDevelopmentKey {
      // The explicit dev override must never query the saved Keychain item.
      hasStoredKey = false
      hasKey = true
      return
    }
    do {
      hasStoredKey = try storedKeyStatus()
      keyError = nil
    } catch {
      hasStoredKey = false
      keyError = error.localizedDescription
    }
    hasKey = hasStoredKey
  }
  var probeBlocker: String? {
    if let keyError { return keyError }
    if !hasKey { return "Add your TypeSafe API key in Settings to ask Jev." }
    if !accessibility { return "Enable Accessibility to give Jev a live desktop snapshot." }
    return nil
  }
  func openPermission(_ pane: String) {
    if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") {
      NSWorkspace.shared.open(url)
    }
  }
}

/// Restores the last external app so Jev never observes its own command composer.
@MainActor
final class DesktopTargetTracker {
  private var lastExternalPID: Int32?
  private var observation: AnyCancellable?
  init() {
    remember(NSWorkspace.shared.frontmostApplication)
    observation = NSWorkspace.shared.notificationCenter.publisher(for: NSWorkspace.didActivateApplicationNotification)
      .sink { [weak self] notification in
        self?.remember(notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication)
      }
  }
  private func remember(_ app: NSRunningApplication?) {
    guard let app, app.processIdentifier != ProcessInfo.processInfo.processIdentifier,
      app.activationPolicy == .regular else { return }
    lastExternalPID = app.processIdentifier
  }
  func prepare(processIdentifier: Int32?) async throws {
    let ownPID = ProcessInfo.processInfo.processIdentifier
    let frontmost = NSWorkspace.shared.frontmostApplication
    if processIdentifier == nil, let frontmost, frontmost.processIdentifier != ownPID {
      remember(frontmost)
      return
    }
    guard let pid = processIdentifier ?? lastExternalPID, pid != ownPID,
      let app = NSRunningApplication(processIdentifier: pid), !app.isTerminated else {
      throw TargetError.unavailable
    }
    if frontmost?.processIdentifier == pid { return }
    guard let url = app.bundleURL else { throw TargetError.unavailable }
    let options = NSWorkspace.OpenConfiguration()
    options.activates = true
    options.allowsRunningApplicationSubstitution = true
    let activated = try await NSWorkspace.shared.openApplication(at: url, configuration: options)
    guard activated.processIdentifier == pid else { throw TargetError.unavailable }
    for _ in 0..<20 {
      try Task.checkCancellation()
      if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid { return }
      try await Task.sleep(for: .milliseconds(50))
    }
    throw TargetError.unavailable
  }
  private enum TargetError: LocalizedError {
    case unavailable
    var errorDescription: String? { "Open the app you want to control, then try again." }
  }
}

/// Creates one service graph for every scene and keeps the lightweight HUD in sync.
@MainActor
final class AppModel: ObservableObject {
  let store: RunStore
  let startup: StorageStartupCoordinator
  let session: SessionCoordinator
  let controller: AutomationController
  let readiness = Readiness()
  let target = DesktopTargetTracker()
  private var overlay: TranscriptPanel?
  private var overlayHideTask: Task<Void, Never>?
  private var subscriptions = Set<AnyCancellable>()

  init() {
    UserDefaults.standard.register(defaults: ["inputPrice": 0.042, "outputPrice": 0.0])
    store = RunStore()
    let store = store
    startup = StorageStartupCoordinator(loaders: [{ await store.load() }])
    let perception = AccessibilityPerception()
    let keyStore = KeychainAPIKeyStore()
    let target = target
    let actionGenerator = ValidActionGenerator(terminalExecutionEnabled: {
      UserDefaults.standard.bool(forKey: "terminalExecutionEnabled")
    })
    controller = AutomationController(
      perception: perception,
      actionGenerator: actionGenerator,
      decisionEngine: JevDecisionEngine { try keyStore.loadFromDevelopmentOverrideOrKeychain() },
      executor: MacOSActionExecutor(perception: perception),
      store: store,
      completionVerifier: DesktopActionCompletionVerifier(),
      prepareTarget: { try await target.prepare(processIdentifier: $0) }
    )
    session = SessionCoordinator(controller: controller, speech: FluidAudioSpeechRecognizer())
    Task { await actionGenerator.prepareInstalledApplications() }
    readiness.refresh()
    overlay = TranscriptPanel(session: session, controller: controller)
    session.objectWillChange.sink { [weak self] in
      Task { @MainActor in self?.updateOverlay() }
    }.store(in: &subscriptions)
    let startup = startup
    Task { await startup.load() }
  }
  private func updateOverlay() {
    overlayHideTask?.cancel()
    guard session.showTranscript else { overlay?.hide(); return }
    if session.state.isActive { overlay?.show(); return }
    guard !session.transcript.isEmpty else { overlay?.hide(); return }
    overlay?.show()
    overlayHideTask = Task { [weak self] in
      try? await Task.sleep(for: .seconds(4))
      guard !Task.isCancelled, let self, !self.session.state.isActive else { return }
      self.overlay?.hide()
    }
  }
}

/// A nonactivating, click-through caption that never steals the desktop target's focus.
@MainActor
final class TranscriptPanel {
  private let panel: NSPanel
  init(session: SessionCoordinator, controller: AutomationController) {
    panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 440, height: 126), styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
    panel.level = .floating
    panel.isOpaque = false
    panel.backgroundColor = .clear
    panel.hasShadow = true
    panel.ignoresMouseEvents = true
    panel.hidesOnDeactivate = false
    panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
    panel.contentView = NSHostingView(rootView: TranscriptHUD(session: session, controller: controller))
  }
  func show() {
    guard !panel.isVisible else { return }
    let screen = NSScreen.screens.first(where: { NSMouseInRect(NSEvent.mouseLocation, $0.frame, false) }) ?? NSScreen.main
    if let frame = screen?.visibleFrame {
      panel.setFrameOrigin(NSPoint(x: frame.midX - 220, y: frame.minY + 30))
    }
    panel.orderFrontRegardless()
  }
  func hide() { panel.orderOut(nil) }
}

/// Keeps the menu bar alive when the main window closes; flushes Stop on explicit Quit.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
  var model: AppModel?
  func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
  func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
    guard let model else { return .terminateNow }
    model.session.stop()
    Task { await model.store.flush(); sender.reply(toApplicationShouldTerminate: true) }
    return .terminateLater
  }
}
