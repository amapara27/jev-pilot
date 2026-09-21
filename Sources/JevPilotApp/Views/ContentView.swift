// A custom command workspace keeps the product identity distinct from stock macOS utilities.
import JevPilotCore
import SwiftUI

/// A fixed, compact rail replaces system sidebar chrome without losing keyboard navigation.
struct ContentView: View {
  @EnvironmentObject private var session: SessionCoordinator
  @EnvironmentObject private var readiness: Readiness
  @Environment(\.scenePhase) private var phase
  @State private var selection = Destination.control
  var body: some View {
    HStack(spacing: 0) {
      VStack(alignment: .leading, spacing: 0) {
        Text("pilot").font(.system(size: 31, weight: .bold)).tracking(-1.7)
          .foregroundStyle(PilotTheme.railText).padding(.horizontal, 23).padding(.top, 36)
        VStack(spacing: 5) {
          ForEach(Destination.allCases) { item in
            Button { selection = item } label: {
              HStack(spacing: 13) {
                Text(item.number).font(PilotTheme.mono(10)).foregroundStyle(selection == item ? PilotTheme.signal : PilotTheme.railMuted)
                Text(item.title).font(.system(size: 13, weight: selection == item ? .semibold : .regular))
                Spacer()
                if selection == item { Rectangle().fill(PilotTheme.signal).frame(width: 4, height: 4) }
              }.foregroundStyle(selection == item ? PilotTheme.railText : PilotTheme.railMuted)
                .padding(.horizontal, 13).padding(.vertical, 13)
                .background(selection == item ? Color.white.opacity(0.075) : .clear, in: RoundedRectangle(cornerRadius: 4))
                .contentShape(Rectangle())
            }.buttonStyle(.plain).accessibilityAddTraits(selection == item ? .isSelected : [])
          }
        }.padding(.horizontal, 12).padding(.top, 42)
        Spacer(minLength: 40)
        VStack(alignment: .leading, spacing: 18) {
          Rectangle().fill(Color.white.opacity(0.12)).frame(height: 1)
          SettingsLink {
            HStack { Text("Settings"); Spacer(); Image(systemName: "arrow.up.right").font(.system(size: 10)) }
              .font(.system(size: 12)).padding(.vertical, 5).contentShape(Rectangle())
          }.buttonStyle(.plain).foregroundStyle(PilotTheme.railText)
        }.padding(23)
      }.frame(width: 180).background(PilotTheme.rail)
      VStack(spacing: 0) {
        Group {
          switch selection {
          case .control: ControlView()
          case .history: HistoryView()
          case .usage: UsageView()
          }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
      }.background(PilotTheme.background).foregroundStyle(PilotTheme.text)
    }.tint(PilotTheme.accent)
      .onChange(of: phase) { _, phase in if phase == .active { readiness.refresh() } }
  }
  private enum Destination: String, Identifiable, CaseIterable {
    case control, history, usage
    var id: Self { self }
    var title: String { rawValue.capitalized }
    var number: String { switch self { case .control: "01"; case .history: "02"; case .usage: "03" } }
  }
}

/// Live state and command entry take priority; raw diagnostics remain collapsed.
struct ControlView: View {
  @EnvironmentObject private var session: SessionCoordinator
  @EnvironmentObject private var controller: AutomationController
  @EnvironmentObject private var store: RunStore
  @EnvironmentObject private var readiness: Readiness
  @State private var command = ""
  @State private var diagnostics = false
  @State private var debugPanel = 0
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 24) {
        Surface {
          VStack(alignment: .leading, spacing: 22) {
            StatusIndicator(state: session.state)
            HStack(alignment: .center) {
              VStack(alignment: .leading, spacing: 8) {
                Text(headline).font(.system(size: 44, weight: .medium)).tracking(-1.8)
              }
              Spacer()
              PilotMark(size: 44).foregroundStyle(PilotTheme.accent)
                .frame(width: 82, height: 82).background(PilotTheme.inset, in: RoundedRectangle(cornerRadius: 5))
            }.padding(.vertical, 8)
            if case .error(let message) = session.state {
              Text(message).font(.callout).foregroundStyle(PilotTheme.danger).textSelection(.enabled)
            }
            ListeningControls()
            if session.showTranscript, !session.transcript.isEmpty {
              Text(session.transcript).font(PilotTheme.mono(13)).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading).padding(14).background(PilotTheme.inset)
            }
            PilotRule()
            HStack(spacing: 12) {
              Text(">").font(PilotTheme.mono(17)).foregroundStyle(PilotTheme.accent).accessibilityHidden(true)
              TextField("Or type a command", text: $command)
                .font(PilotTheme.mono(12)).textFieldStyle(.plain).onSubmit(runTyped).disabled(session.state.isActive)
                .accessibilityLabel("Typed command")
              Button(action: runTyped) { Image(systemName: "arrow.up.right").frame(width: 16, height: 16) }
                .buttonStyle(PilotButtonStyle())
                .disabled(session.state.isActive || command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .help("Run typed command").accessibilityLabel("Run typed command")
            }
          }
        }
        if let blocker = readiness.commandBlocker {
          HStack(alignment: .center, spacing: 12) {
            Image(systemName: "circle.lefthalf.filled").foregroundStyle(PilotTheme.muted).accessibilityHidden(true)
            Text(blocker).font(.system(size: 12)).foregroundStyle(PilotTheme.muted)
            Spacer(minLength: 8)
            SettingsLink { Text("Set up ↗") }.buttonStyle(PilotButtonStyle())
          }
        }
        if let error = store.errorMessage { Text(error).font(.callout).foregroundStyle(PilotTheme.danger) }
        ConfirmationView()
        VStack(alignment: .leading, spacing: 16) {
          SectionCaption(title: controller.currentRun == nil ? "Activity" : controller.currentRun?.outcome == nil ? "Current run" : "Last run")
          PilotRule()
          if let run = controller.currentRun {
            HStack(alignment: .top) {
              Text(run.command).font(.system(size: 19, weight: .medium)).textSelection(.enabled)
              Spacer(); OutcomeLabel(outcome: run.outcome)
            }
            if run.outcome == nil, let decision = controller.latestDecision {
              Text(decision.candidate.action.summary).font(.callout).foregroundStyle(PilotTheme.muted)
            }
            HStack {
              if run.outcome == nil {
                SwiftUI.TimelineView(.periodic(from: .now, by: 1)) { context in
                  MetricView(title: "Elapsed", value: durationLabel(context.date.timeIntervalSince(run.startedAt)))
                }
              } else { MetricView(title: "Duration", value: durationLabel(run.duration)) }
              MetricView(title: "Step", value: stepLabel)
              MetricView(title: "Actions", value: "\(run.actionCount)")
              MetricView(title: "Decision", value: controller.latestDecision.map { "\($0.latencyMilliseconds) ms" } ?? "—")
            }.padding(.vertical, 8)
            if !run.events.isEmpty { RunEventList(events: run.events) }
            else { Text("Observing the desktop…").font(.callout).foregroundStyle(PilotTheme.muted) }
          } else {
            HStack(alignment: .top, spacing: 16) {
              Text("—").font(PilotTheme.mono(24)).foregroundStyle(PilotTheme.muted)
              VStack(alignment: .leading, spacing: 6) {
                Text("No runs yet").font(.system(size: 13)).foregroundStyle(PilotTheme.muted)
              }
            }.padding(.vertical, 16)
          }
        }
        DisclosureGroup("Live diagnostics", isExpanded: $diagnostics) {
          if diagnostics {
            VStack(spacing: 12) {
              Picker("Diagnostics", selection: $debugPanel) {
                Text("Events").tag(0); Text("Desktop state").tag(1); Text("Candidates").tag(2)
              }.labelsHidden()
              Group {
                switch debugPanel {
                case 1: JSONDebugView(title: "Desktop state", value: controller.latestState)
                case 2: ActionsView(actions: controller.availableActions, decision: controller.latestDecision)
                default: TimelineView(events: controller.debugEvents)
                }
              }.frame(height: 300)
            }.padding(.top, 12)
          }
        }.font(PilotTheme.mono(11)).foregroundStyle(PilotTheme.muted)
      }.padding(30).frame(maxWidth: 980, alignment: .leading).frame(maxWidth: .infinity)
    }.foregroundStyle(PilotTheme.text).background(PilotTheme.background)
  }
  private var headline: String {
    switch session.state {
    case .stopped: "Ready."
    case .listening: "Listening."
    case .executing: "On it."
    case .awaitingConfirmation: "Your call."
    case .error: "Let’s reconnect."
    }
  }
  private var stepLabel: String {
    if case .running(let step) = controller.status { return "\(step)/12" }
    if let pending = controller.pendingConfirmation { return "\(pending.nextStep - 1)/12" }
    return "—"
  }
  private func runTyped() { session.runTyped(command) }
}

struct OutcomeLabel: View {
  let outcome: RunOutcome?
  var body: some View {
    HStack(spacing: 5) {
      Rectangle().fill(color).frame(width: 4, height: 4)
      Text(outcome?.rawValue.uppercased() ?? "RUNNING").font(PilotTheme.mono(9))
    }.foregroundStyle(color).padding(.vertical, 4)
  }
  private var color: Color {
    switch outcome { case .completed: PilotTheme.accent; case .failed, .blocked: PilotTheme.danger; case .interrupted, .rejected: PilotTheme.muted; case .stopped: PilotTheme.muted; case nil: PilotTheme.accent }
  }
}
