// Shared controls use a restrained instrument-panel visual language.
import JevPilotCore
import SwiftUI

struct Surface<Content: View>: View {
  @ViewBuilder var content: Content
  var body: some View {
    content.padding(22).frame(maxWidth: .infinity, alignment: .leading)
      .background(PilotTheme.surface, in: RoundedRectangle(cornerRadius: 14))
      .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(PilotTheme.line))
  }
}

struct StatusIndicator: View {
  let state: SessionCoordinator.State
  var body: some View {
    HStack(spacing: 8) {
      Circle().fill(color).frame(width: 6, height: 6)
      Text(label.uppercased()).font(PilotTheme.mono(10)).tracking(0.9)
    }.accessibilityElement(children: .combine)
  }
  private var color: Color {
    switch state {
    case .preparingModel, .listening, .askingJev, .running, .awaitingConfirmation: PilotTheme.accent
    case .complete: PilotTheme.signal
    case .error, .blocked: PilotTheme.danger
    case .stopped, .rejected: PilotTheme.muted
    }
  }
  private var label: String {
    switch state { case .error: "Needs attention"; case .blocked: "Blocked"; case .stopped: "Standby"; default: state.label }
  }
}

struct MetricView: View {
  let title: String
  let value: String
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      Text(title.uppercased()).font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted).tracking(0.7)
      Text(value).font(PilotTheme.label(25, weight: .regular)).foregroundStyle(PilotTheme.text).monospacedDigit()
        .lineLimit(1).minimumScaleFactor(0.6)
    }.frame(maxWidth: .infinity, alignment: .leading)
  }
}

struct ListeningControls: View {
  @EnvironmentObject private var session: SessionCoordinator
  var compact = false
  var body: some View {
    HStack {
      Button(action: primaryAction) {
        HStack(spacing: 10) {
          Image(systemName: primaryIcon).accessibilityHidden(true)
          Text(primaryLabel)
          Spacer(minLength: 0)
          if !session.state.isActive {
            Image(systemName: "arrow.up.right").font(.system(size: 10)).accessibilityHidden(true)
          }
        }
      }.buttonStyle(PilotButtonStyle(prominent: true, destructive: isCancelling))
        .frame(maxWidth: compact ? .infinity : 205)
        .help(primaryHelp)
      if !compact { Spacer(minLength: 0) }
    }
  }

  private var isListening: Bool { session.state == .listening }
  private var isCancelling: Bool { session.state.isActive && !isListening }
  private var primaryLabel: String {
    if isListening { return "Finish recording" }
    if session.state == .awaitingConfirmation { return "Stop run" }
    if case .running = session.state { return "Stop run" }
    return session.state.isActive ? "Cancel" : "Start listening"
  }
  private var primaryIcon: String {
    if isListening { return "stop.fill" }
    return session.state.isActive ? "xmark" : "mic"
  }
  private var primaryHelp: String {
    if isListening { return "Finish recording and transcribe" }
    return session.state.isActive ? "Cancel the current session (⌘.)" : "Start listening (⇧⌘L)"
  }
  private func primaryAction() {
    if isListening { session.finishListening() }
    else if session.state.isActive { session.stop() }
    else { session.startListening() }
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
