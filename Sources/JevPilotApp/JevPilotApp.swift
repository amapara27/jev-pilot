// Exposes a single control center, shared Settings, and a persistent macOS menu bar panel.
import JevPilotCore
import SwiftUI

@main
struct JevPilotApp: App {
  @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
  @StateObject private var model = AppModel()
  var body: some Scene {
    Window("Jev Pilot", id: "control-center") {
      ContentView()
        .environmentObject(model)
        .environmentObject(model.session)
        .environmentObject(model.controller)
        .environmentObject(model.store)
        .environmentObject(model.readiness)
        .frame(minWidth: 780, minHeight: 580)
        .onAppear { delegate.model = model }
    }
    .defaultSize(width: 1040, height: 730)
    .windowStyle(.hiddenTitleBar)
    .commands {
      CommandMenu("Control") {
        Button("Start Listening") { model.session.startListening() }
          .keyboardShortcut("l", modifiers: [.command, .shift])
        Button("Stop") { model.session.stop() }
          .keyboardShortcut(".", modifiers: .command)
      }
    }
    MenuBarExtra {
      MenuBarPanel()
        .environmentObject(model.session)
        .environmentObject(model.controller)
        .environmentObject(model.store)
        .environmentObject(model.readiness)
    } label: {
      MenuBarIcon(session: model.session)
    }
    .menuBarExtraStyle(.window)
    Settings {
      SettingsView()
        .environmentObject(model.session)
        .environmentObject(model.store)
        .environmentObject(model.readiness)
    }
  }
}
