// A compact companion shares the control center's identity and session controls.
import AppKit
import JevPilotCore
import SwiftUI

struct MenuBarIcon: View {
  @ObservedObject var session: SessionCoordinator
  var body: some View {
    Image(systemName: icon).accessibilityLabel("Pilot: \(session.state.label)")
  }
  private var icon: String {
    switch session.state {
    case .preparingModel: "arrow.down.circle"
    case .listening: "mic.fill"
    case .askingJev: "waveform"
    case .complete: "checkmark.circle"
    case .error: "exclamationmark.triangle"
    case .stopped: "waveform.and.mic"
    }
  }
}

struct MenuBarPanel: View {
  @EnvironmentObject private var session: SessionCoordinator
  @EnvironmentObject private var readiness: Readiness
  @Environment(\.openWindow) private var openWindow
  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack {
        Text("pilot").font(PilotTheme.label(24, weight: .bold)).tracking(-1)
        Spacer()
        PilotMark(size: 19).foregroundStyle(PilotTheme.signal)
      }.foregroundStyle(PilotTheme.railText).padding(20).background(PilotTheme.rail)
      VStack(alignment: .leading, spacing: 17) {
        StatusIndicator(state: session.state)
        if case .error(let message) = session.state {
          Text(message).font(.caption).foregroundStyle(PilotTheme.danger).fixedSize(horizontal: false, vertical: true)
        }
        ListeningControls(compact: true)
        if !session.transcript.isEmpty {
          Text(session.transcript).font(.callout).lineLimit(3)
        }
        if let decision = session.probeResult?.decision {
          Text(decision.candidate.action.summary).font(.caption).foregroundStyle(PilotTheme.muted).lineLimit(2)
        }
        PilotRule()
        Button {
          openWindow(id: "control-center")
          NSApp.activate(ignoringOtherApps: true)
        } label: {
          HStack { Text("Open control center"); Spacer(); Image(systemName: "arrow.up.right") }
            .font(.system(size: 12, weight: .medium)).padding(.vertical, 4).contentShape(Rectangle())
        }.buttonStyle(.plain)
        HStack {
          SettingsLink { Text("Settings") }.buttonStyle(.plain)
          Spacer()
          Button("Quit") { NSApp.terminate(nil) }.buttonStyle(.plain)
        }.font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
      }.padding(20)
    }.frame(width: 330).foregroundStyle(PilotTheme.text).background(PilotTheme.background)
      .onAppear { readiness.refresh() }
  }
}

struct TranscriptHUD: View {
  @ObservedObject var session: SessionCoordinator
  var body: some View {
    HStack(spacing: 16) {
      PilotMark(size: 23).foregroundStyle(PilotTheme.accent)
      VStack(alignment: .leading, spacing: 9) {
        StatusIndicator(state: session.state)
        Text(session.transcript.isEmpty ? "Say a command…" : session.transcript)
          .font(.system(size: 13)).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
      }
    }.padding(18).frame(width: 440, height: 90).foregroundStyle(PilotTheme.text)
      .background(PilotTheme.surface, in: RoundedRectangle(cornerRadius: 7))
      .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(PilotTheme.line))
  }
}
