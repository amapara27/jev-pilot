// Exposes a single control center, shared Settings, and a persistent macOS menu bar panel.
import JevPilotCore
import SwiftUI

@main
struct JevPilotApp: App {
  @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
  @StateObject private var model = AppModel()
  var body: some Scene {
    Window("Jev Pilot", id: "control-center") {
      StartupGate(startup: model.startup) { ContentView() }
        .environmentObject(model)
        .environmentObject(model.session)
        .environmentObject(model.store)
        .environmentObject(model.readiness)
        .frame(minWidth: 780, minHeight: 580)
        .onAppear { delegate.model = model }
    }
    .defaultSize(width: 1040, height: 730)
    .windowStyle(.hiddenTitleBar)
    .commands {
      PilotCommands(startup: model.startup, session: model.session)
    }
    MenuBarExtra {
      StartupGate(startup: model.startup) { MenuBarPanel() }
        .environmentObject(model.session)
        .environmentObject(model.store)
        .environmentObject(model.readiness)
    } label: {
      MenuBarIcon(session: model.session)
    }
    .menuBarExtraStyle(.window)
    Settings {
      StartupGate(startup: model.startup) { SettingsView() }
        .environmentObject(model.session)
        .environmentObject(model.store)
        .environmentObject(model.readiness)
    }
  }
}

/// Hides command shortcuts until the same startup gate used by every visible surface opens.
struct PilotCommands: Commands {
  @ObservedObject var startup: StorageStartupCoordinator
  let session: SessionCoordinator

  var body: some Commands {
    CommandMenu("Control") {
      if startup.isReady {
        Button("Start Listening") { session.startListening() }
          .keyboardShortcut("l", modifiers: [.command, .shift])
        Button("Stop") { session.stop() }
          .keyboardShortcut(".", modifiers: .command)
      } else {
        Button("Loading…") {}
          .disabled(true)
      }
    }
  }
}
