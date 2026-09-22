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

  private let storedKeyStatus: () throws -> Bool
  private let environmentKeyStatus: () -> Bool

  init(
    storedKeyStatus: @escaping () throws -> Bool = {
      try KeychainAPIKeyStore().containsKey()
    },
    environmentKeyStatus: @escaping () -> Bool = {
      ProcessInfo.processInfo.environment["TYPESAFE_API_KEY"]?.isEmpty == false
    }
  ) {
    self.storedKeyStatus = storedKeyStatus
    self.environmentKeyStatus = environmentKeyStatus
  }

  func refresh() {
    accessibility = AXIsProcessTrusted()
    microphone = AVCaptureDevice.authorizationStatus(for: .audio)
    do {
      hasStoredKey = try storedKeyStatus()
    } catch {
      hasStoredKey = false
    }
    hasKey = hasStoredKey || environmentKeyStatus()
  }
  var probeBlocker: String? {
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
    let readiness = readiness
    let target = target
    let probe = JevGoalProbe(
      perception: perception,
      decisionEngine: JevDecisionEngine { try keyStore.loadFromKeychainOrEnvironment() },
      readiness: {
        readiness.refresh()
        return readiness.probeBlocker
      },
      prepareTarget: { try await target.prepare(processIdentifier: nil) }
    )
    session = SessionCoordinator(probe: probe, speech: FluidAudioSpeechRecognizer())
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
