// Shared controls use a restrained instrument-panel visual language.
import JevPilotCore
import SwiftUI

struct Surface<Content: View>: View {
  @ViewBuilder var content: Content
  var body: some View {
    content.padding(22).frame(maxWidth: .infinity, alignment: .leading)
      .background(PilotTheme.surface, in: RoundedRectangle(cornerRadius: 7))
      .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(PilotTheme.line))
  }
}

struct StatusIndicator: View {
  let state: SessionCoordinator.State
  var body: some View {
    HStack(spacing: 8) {
      Rectangle().fill(color).frame(width: 6, height: 6)
      Text(label.uppercased()).font(PilotTheme.mono(10)).tracking(0.9)
    }.accessibilityElement(children: .combine)
  }
  private var color: Color {
    switch state { case .listening, .executing: PilotTheme.accent; case .awaitingConfirmation: .orange; case .error: PilotTheme.danger; case .stopped: PilotTheme.muted }
  }
  private var label: String {
    switch state { case .error: "Needs attention"; case .stopped: "Standby"; default: state.label }
  }
}

struct MetricView: View {
  let title: String
  let value: String
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      Text(title.uppercased()).font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted).tracking(0.7)
      Text(value).font(PilotTheme.mono(23, weight: .regular)).foregroundStyle(PilotTheme.text).monospacedDigit()
    }.frame(maxWidth: .infinity, alignment: .leading)
  }
}

struct ConfirmationView: View {
  @EnvironmentObject private var session: SessionCoordinator
  @EnvironmentObject private var controller: AutomationController
  var body: some View {
    if let pending = controller.pendingConfirmation {
      VStack(alignment: .leading, spacing: 12) {
        Label("Confirmation required", systemImage: "hand.raised").font(.headline)
        Text(pending.decision.candidate.action.summary).font(.callout.weight(.medium))
        Text(pending.assessment.reason).font(.callout).foregroundStyle(PilotTheme.muted)
        HStack {
          Button("Reject", role: .cancel) { session.reject() }.buttonStyle(PilotButtonStyle())
          Button("Allow action") { session.confirm() }.buttonStyle(PilotButtonStyle(prominent: true))
        }.disabled(session.state != .awaitingConfirmation)
      }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
        .background(PilotTheme.surface)
        .overlay(alignment: .leading) { Rectangle().fill(.orange).frame(width: 3) }
        .accessibilityElement(children: .contain)
    }
  }
}

/// Mode buttons have explicit selected semantics instead of a stock segmented picker.
struct ListeningModeSelector: View {
  @EnvironmentObject private var session: SessionCoordinator
  var body: some View {
    HStack(spacing: 2) {
      ForEach(ListeningMode.allCases) { mode in
        Button { session.mode = mode } label: {
          Text(mode == .single ? "Single" : "Continuous")
            .font(.system(size: 12, weight: .medium)).frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .foregroundStyle(session.mode == mode ? PilotTheme.text : PilotTheme.muted)
            .background(session.mode == mode ? PilotTheme.surface : .clear, in: RoundedRectangle(cornerRadius: 3))
            .overlay(RoundedRectangle(cornerRadius: 3).strokeBorder(session.mode == mode ? PilotTheme.line : .clear))
        }.buttonStyle(.plain)
          .accessibilityLabel(mode.title)
          .accessibilityAddTraits(session.mode == mode ? .isSelected : [])
      }
    }.padding(3).background(PilotTheme.inset, in: RoundedRectangle(cornerRadius: 5))
      .disabled(session.state.isActive).opacity(session.state.isActive ? 0.55 : 1)
      .accessibilityElement(children: .contain).accessibilityLabel("Listening mode")
  }
}

struct ListeningControls: View {
  @EnvironmentObject private var session: SessionCoordinator
  @EnvironmentObject private var store: RunStore
  var compact = false
  var body: some View {
    let layout = compact ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12)) : AnyLayout(HStackLayout(spacing: 14))
    layout {
      ListeningModeSelector().frame(maxWidth: compact ? .infinity : 225)
      Button { session.state.isActive ? session.stop() : session.startListening() } label: {
        HStack(spacing: 10) {
          Image(systemName: session.state.isActive ? "stop.fill" : "mic").accessibilityHidden(true)
          Text(session.state.isActive ? "Stop session" : "Start listening")
          Spacer(minLength: 0)
          Image(systemName: session.state.isActive ? "xmark" : "arrow.up.right").font(.system(size: 10)).accessibilityHidden(true)
        }
      }.buttonStyle(PilotButtonStyle(prominent: true, destructive: session.state.isActive))
        .disabled(!store.isLoaded).frame(maxWidth: compact ? .infinity : 205)
        .help(session.state.isActive ? "Stop listening and cancel the run (⌘.)" : "Start listening (⇧⌘L)")
      if !compact { Spacer(minLength: 0) }
    }
  }
}

struct RunEventList: View {
  let events: [RunEvent]
  var body: some View {
    LazyVStack(alignment: .leading, spacing: 0) {
      ForEach(Array(events.enumerated()), id: \.element.id) { index, event in
        HStack(alignment: .top, spacing: 14) {
          Text(String(format: "%02d", index + 1)).font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted).padding(.top, 3)
          VStack(alignment: .leading, spacing: 4) {
            Text(event.title).font(.system(size: 13, weight: .medium))
            if !event.detail.isEmpty { Text(event.detail).font(.system(size: 12)).foregroundStyle(PilotTheme.muted) }
          }
          Spacer(minLength: 12)
          if event.succeeded == true { Image(systemName: "checkmark").font(.caption).foregroundStyle(PilotTheme.accent).accessibilityLabel("Succeeded") }
          Text(event.timestamp, style: .time).font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
        }.padding(.vertical, 13)
        if event.id != events.last?.id { PilotRule() }
      }
    }.foregroundStyle(PilotTheme.text).textSelection(.enabled)
  }
}

func durationLabel(_ seconds: TimeInterval) -> String {
  let total = Int(max(0, seconds))
  return total < 60 ? "\(total)s" : "\(total / 60)m \(total % 60)s"
}
func costLabel(_ amount: Double) -> String {
  amount == 0 ? "$0.00" : amount < 0.000001 ? "< $0.000001" : String(format: "$%.6f", amount)
}
