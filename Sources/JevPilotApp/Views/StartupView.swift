// Keeps every command surface behind the shared storage-readiness boundary.
import JevPilotCore
import SwiftUI

/// Switches atomically from a minimal loader to its real application surface.
struct StartupGate<Content: View>: View {
  @ObservedObject var startup: StorageStartupCoordinator
  @ViewBuilder let content: () -> Content

  var body: some View {
    if startup.isReady {
      content()
    } else {
      StartupLoadingView()
    }
  }
}

/// A deliberately sparse loading surface with no automation controls.
struct StartupLoadingView: View {
  var body: some View {
    VStack(spacing: 18) {
      PilotMark(size: 30).foregroundStyle(PilotTheme.accent)
      ProgressView().controlSize(.small)
      Text("Loading…")
        .font(PilotTheme.mono(10))
        .foregroundStyle(PilotTheme.muted)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .foregroundStyle(PilotTheme.text)
    .background(PilotTheme.background)
    .accessibilityElement(children: .combine)
    .accessibilityLabel("Jev Pilot is loading")
  }
}
