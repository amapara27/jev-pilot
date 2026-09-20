import JevPilotCore
import SwiftUI

@main
struct JevPilotApp: App {
  @StateObject private var controller: AutomationController
  @StateObject private var speechRecognizer = LocalSpeechRecognizer()

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
