// Defines the SwiftUI app entry point and wires together production services.
import JevPilotCore
import SwiftUI

/// Creates shared automation services and the app's main scenes.
@main
struct JevPilotApp: App {
  @StateObject private var controller: AutomationController
  @StateObject private var speechRecognizer = LocalSpeechRecognizer()

  /// Connects perception, decision, and execution through one controller.
  init() {
    let perception = AccessibilityPerception()
    let keyStore = KeychainAPIKeyStore()
    let decisionEngine = JevDecisionEngine {
      try keyStore.loadFromKeychainOrEnvironment()
    }
    _controller = StateObject(
      wrappedValue: AutomationController(
        perception: perception,
        decisionEngine: decisionEngine,
        executor: MacOSActionExecutor(perception: perception)
      ))
  }

  var body: some Scene {
    WindowGroup {
      ContentView(controller: controller, speechRecognizer: speechRecognizer)
        .frame(minWidth: 980, minHeight: 680)
    }
    .defaultSize(width: 1_140, height: 760)

    Settings {
      SettingsView()
    }
  }
}
