// A compact companion shares the control center's identity and session controls.
import AppKit
import JevPilotCore
import SwiftUI

struct MenuBarIcon: View {
  @ObservedObject var session: SessionCoordinator
  @ObservedObject var controller: AutomationController
  var body: some View {
    Image(systemName: icon).accessibilityLabel("Pilot: \(session.state.label), \(controller.status.label), \(session.queuedGoals.count) queued")
  }
  private var icon: String {
    if session.queuePaused { return "pause.circle" }
    if session.captureState == .listening {
      switch controller.status {
      case .awaitingConfirmation: return "questionmark.circle"
      case .awaitingAppChoice: return "square.stack"
      case .running: return "gearshape.2"
      default: break
      }
    }
    return switch session.state {
    case .preparingModel: "arrow.down.circle"
    case .listening: "mic.fill"
    case .askingJev: "waveform"
    case .running: "gearshape.2"
    case .awaitingConfirmation: "questionmark.circle"
    case .awaitingAppChoice: "square.stack"
    case .complete: "checkmark.circle"
    case .error, .blocked: "exclamationmark.triangle"
    case .stopped, .rejected: "waveform.and.mic"
    }
  }
}

struct MenuBarPanel: View {
  @EnvironmentObject private var session: SessionCoordinator
  @EnvironmentObject private var controller: AutomationController
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
        if case .blocked(let message) = session.state {
          Text(message).font(.caption).foregroundStyle(PilotTheme.danger).fixedSize(horizontal: false, vertical: true)
        }
        ListeningControls(compact: true)
        if session.activeGoal != nil || !session.queuedGoals.isEmpty || session.queuePaused {
          HStack {
            Text(session.queuePaused ? "Queue paused" : controller.status.label)
            Spacer()
            Text("\(session.queuedGoals.count) queued")
          }.font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
          if session.queuePaused {
            if let reason = session.pauseReason {
              Text(reason).font(.caption).foregroundStyle(PilotTheme.muted).lineLimit(2)
            }
            Button("Resume queue") { session.resumeQueue() }.buttonStyle(PilotButtonStyle())
          }
        }
        if !session.transcript.isEmpty {
          Text(session.transcript).font(.callout).lineLimit(3)
        }
        if let decision = controller.latestDecision {
          HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(decision.candidate.action.summary).lineLimit(2)
            Spacer(minLength: 0)
            Text((decision.probabilities[decision.candidate.id] ?? 0).formatted(.percent.precision(.fractionLength(1))))
              .font(PilotTheme.mono(10)).monospacedDigit()
          }.font(.caption).foregroundStyle(PilotTheme.muted)
          VStack(spacing: 5) {
            ForEach(Array(controller.availableActions.sorted {
              (decision.probabilities[$0.id] ?? 0) > (decision.probabilities[$1.id] ?? 0)
            }.filter { $0.id != decision.candidate.id }.prefix(2))) { candidate in
              HStack {
                Text(candidate.action.summary).lineLimit(1)
                Spacer(minLength: 6)
                Text((decision.probabilities[candidate.id] ?? 0).formatted(.percent.precision(.fractionLength(1))))
                  .monospacedDigit()
              }.font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
            }
          }
        }
        if let lastAction = controller.history.last {
          Text(lastAction.message).font(.caption).foregroundStyle(PilotTheme.muted).lineLimit(2)
        }
        if let pending = controller.pendingConfirmation {
          Text(pending.assessment.reason).font(.caption).foregroundStyle(PilotTheme.muted)
          if case .terminalRun = pending.decision.candidate.action {
            Button("Review command in Control") {
              openWindow(id: "control-center")
              NSApp.activate(ignoringOtherApps: true)
            }.buttonStyle(PilotButtonStyle())
          } else {
            HStack(spacing: 8) {
              Button("Approve") { session.confirmPendingAction() }.buttonStyle(PilotButtonStyle(prominent: true))
              Button("Reject") { session.rejectPendingAction() }.buttonStyle(PilotButtonStyle())
            }
          }
        }
        if !controller.appChoices.isEmpty {
          ForEach(controller.appChoices) { choice in
            switch choice.action {
            case .openApp(let bundleID, let name), .focusApp(let bundleID, let name):
              Button("\(name) · \(bundleID)") { session.chooseApplication(bundleID) }
                .buttonStyle(PilotButtonStyle())
            default: EmptyView()
            }
          }
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
  @ObservedObject var controller: AutomationController
  var body: some View {
    HStack(spacing: 16) {
      PilotMark(size: 23).foregroundStyle(PilotTheme.accent)
      VStack(alignment: .leading, spacing: 5) {
        StatusIndicator(state: session.state)
        if session.activeGoal != nil || !session.queuedGoals.isEmpty || session.queuePaused {
          Text("\(session.queuePaused ? "Queue paused" : controller.status.label) · \(session.queuedGoals.count) queued")
            .font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
        }
        Text(session.transcript.isEmpty ? "Say a command…" : session.transcript)
          .font(.system(size: 13)).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
        if let summary = controller.history.last?.action.summary ?? controller.latestDecision?.candidate.action.summary {
          Text(summary).font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted).lineLimit(1)
        }
      }
    }.padding(16).frame(width: 440, height: 126).foregroundStyle(PilotTheme.text)
      .background(PilotTheme.surface, in: RoundedRectangle(cornerRadius: 7))
      .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(PilotTheme.line))
  }
}
