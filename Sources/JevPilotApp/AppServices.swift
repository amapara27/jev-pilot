// Provides macOS permissions, foreground targeting, and shared application services.
import AppKit
import ApplicationServices
import AVFoundation
import Combine
import JevPilotCore
import Speech
import SwiftUI

/// Reads permission state without prompting at app launch.
@MainActor
final class Readiness: ObservableObject {
  @Published var accessibility = false
  @Published var microphone = AVAuthorizationStatus.notDetermined
  @Published var speech = SFSpeechRecognizerAuthorizationStatus.notDetermined
  @Published var hasKey = false
  func refresh() {
    accessibility = AXIsProcessTrusted()
    microphone = AVCaptureDevice.authorizationStatus(for: .audio)
    speech = SFSpeechRecognizer.authorizationStatus()
    hasKey = (try? KeychainAPIKeyStore().loadFromKeychainOrEnvironment())?.isEmpty == false
  }
  var commandBlocker: String? {
    if !hasKey { return "Add your TypeSafe API key in Settings to begin." }
    if !accessibility { return "Enable Accessibility in System Settings to control your desktop." }
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
    guard app.activate(options: [.activateAllWindows]) else { throw TargetError.unavailable }
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
  let controller: AutomationController
  let session: SessionCoordinator
  let readiness = Readiness()
  let target = DesktopTargetTracker()
  private var overlay: TranscriptPanel?
  private var subscriptions = Set<AnyCancellable>()

  init() {
    UserDefaults.standard.register(defaults: ["inputPrice": 0.042, "outputPrice": 0.0])
    store = RunStore()
    let store = store
    startup = StorageStartupCoordinator(loaders: [{ await store.load() }])
    let perception = AccessibilityPerception()
    let keyStore = KeychainAPIKeyStore()
    controller = AutomationController(perception: perception, decisionEngine: JevDecisionEngine {
      try keyStore.loadFromKeychainOrEnvironment()
    }, executor: MacOSActionExecutor(perception: perception), store: store)
    let readiness = readiness
    let target = target
    let controller = controller
    session = SessionCoordinator(controller: controller, speech: LocalSpeechRecognizer(), readiness: {
      readiness.refresh()
      controller.pricing = TokenPricing(inputPerMillion: UserDefaults.standard.double(forKey: "inputPrice"), outputPerMillion: UserDefaults.standard.double(forKey: "outputPrice"))
      return readiness.commandBlocker
    }, prepareTarget: { pid in try await target.prepare(processIdentifier: pid) })
    readiness.refresh()
    overlay = TranscriptPanel(session: session)
    session.objectWillChange.sink { [weak self] in
      Task { @MainActor in self?.updateOverlay() }
    }.store(in: &subscriptions)
    let startup = startup
    Task { await startup.load() }
  }
  private func updateOverlay() {
    if session.showTranscript && session.state.isActive { overlay?.show() }
    else { overlay?.hide() }
  }
}

/// A nonactivating, click-through caption that never steals the desktop target's focus.
@MainActor
final class TranscriptPanel {
  private let panel: NSPanel
  init(session: SessionCoordinator) {
    panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 440, height: 90), styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
    panel.level = .floating
    panel.isOpaque = false
    panel.backgroundColor = .clear
    panel.hasShadow = true
    panel.ignoresMouseEvents = true
    panel.hidesOnDeactivate = false
    panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
    panel.contentView = NSHostingView(rootView: TranscriptHUD(session: session))
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
