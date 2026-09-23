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
        Text("pilot").font(PilotTheme.label(31, weight: .bold)).tracking(-1.7)
          .foregroundStyle(PilotTheme.railText).padding(.horizontal, 23).padding(.top, 36)
        VStack(spacing: 5) {
          ForEach(Destination.allCases) { item in
            Button { selection = item } label: {
              HStack(spacing: 13) {
                Text(item.number).font(PilotTheme.mono(10)).foregroundStyle(selection == item ? PilotTheme.signal : PilotTheme.railMuted)
                Text(item.title).font(PilotTheme.label(13, weight: selection == item ? .semibold : .regular))
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

/// Voice capture and the validated decision are the primary workspace surfaces.
struct ControlView: View {
  @EnvironmentObject private var session: SessionCoordinator
  @EnvironmentObject private var controller: AutomationController
  @EnvironmentObject private var store: RunStore
  @EnvironmentObject private var readiness: Readiness
  @State private var expected = ""
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 24) {
        Surface {
          VStack(alignment: .leading, spacing: 22) {
            StatusIndicator(state: session.state)
            HStack(alignment: .center) {
              VStack(alignment: .leading, spacing: 8) {
                Text(headline).font(PilotTheme.display(48)).tracking(-1.8)
                  .lineLimit(1).minimumScaleFactor(0.65)
              }
              Spacer()
              PilotMark(size: 44).foregroundStyle(PilotTheme.accent)
                .frame(width: 82, height: 82).background(PilotTheme.inset, in: RoundedRectangle(cornerRadius: 18))
            }.padding(.vertical, 8)
            if case .error(let message) = session.state {
              Text(message).font(.callout).foregroundStyle(PilotTheme.danger).textSelection(.enabled)
            }
            if case .blocked(let message) = session.state {
              Text(message).font(.callout).foregroundStyle(PilotTheme.danger).textSelection(.enabled)
            }
            ListeningControls()
            if session.activeGoal != nil || !session.queuedGoals.isEmpty || session.queuePaused {
              HStack(spacing: 10) {
                Text(controller.status.label).font(PilotTheme.mono(11))
                Text("\(session.queuedGoals.count) queued").font(PilotTheme.mono(11)).foregroundStyle(PilotTheme.muted)
                Spacer()
                if session.queuePaused {
                  Button("Resume") { session.resumeQueue() }.buttonStyle(PilotButtonStyle())
                }
              }
              if let reason = session.pauseReason {
                Text(reason).font(.caption).foregroundStyle(PilotTheme.muted)
              }
              ForEach(session.queuedGoals) { goal in
                Text(goal.text).font(.caption).lineLimit(2).foregroundStyle(PilotTheme.muted)
              }
            }
            if !session.transcript.isEmpty {
              PilotRule()
              Text(session.transcript).font(.system(size: 16, weight: .regular)).lineSpacing(4).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
          }
        }
        if let blocker = readiness.probeBlocker {
          HStack(alignment: .center, spacing: 12) {
            Image(systemName: "circle.lefthalf.filled").foregroundStyle(PilotTheme.muted).accessibilityHidden(true)
            Text(blocker).font(.system(size: 12)).foregroundStyle(PilotTheme.muted)
            Spacer(minLength: 8)
            SettingsLink { Text("Set up ↗") }.buttonStyle(PilotButtonStyle())
          }
        }
        if let error = store.errorMessage { Text(error).font(.callout).foregroundStyle(PilotTheme.danger) }
        if let pending = controller.pendingConfirmation {
          Surface {
            VStack(alignment: .leading, spacing: 14) {
              SectionCaption(title: "Confirm action")
              Text(pending.decision.candidate.action.summary).font(PilotTheme.label(21)).textSelection(.enabled)
              Text(pending.assessment.reason).font(.system(size: 12)).foregroundStyle(PilotTheme.muted)
              if case .terminalRun(let command) = pending.decision.candidate.action {
                TerminalApprovalView(command: command)
              } else {
                HStack(spacing: 10) {
                  Button("Approve") { session.confirmPendingAction() }.buttonStyle(PilotButtonStyle(prominent: true))
                  Button("Reject") { session.rejectPendingAction() }.buttonStyle(PilotButtonStyle())
                }
              }
            }
          }
        }
        if !controller.appChoices.isEmpty {
          Surface {
            VStack(alignment: .leading, spacing: 10) {
              SectionCaption(title: "Choose app")
              ForEach(controller.appChoices) { choice in
                switch choice.action {
                case .openApp(let bundleID, let name), .focusApp(let bundleID, let name):
                  Button("\(name) · \(bundleID)") { session.chooseApplication(bundleID) }
                    .buttonStyle(PilotButtonStyle())
                default: EmptyView()
                }
              }
            }
          }
        }
        if let run = controller.currentRun, !run.events.isEmpty {
          Surface {
            VStack(alignment: .leading, spacing: 12) {
              SectionCaption(title: "Run", trailing: run.outcome?.rawValue.capitalized ?? controller.status.label)
              RunEventList(events: run.events)
            }
          }
        }
        VStack(alignment: .leading, spacing: 16) {
          SectionCaption(title: "Decision", trailing: controller.status.label)
          PilotRule()
          if let decision = controller.latestDecision {
            HStack(alignment: .top) {
              Image(systemName: "arrow.turn.down.right").foregroundStyle(PilotTheme.accent).accessibilityHidden(true)
              Text(decision.candidate.action.summary).font(PilotTheme.label(21)).textSelection(.enabled)
              Spacer(minLength: 0)
            }
            HStack {
              MetricView(title: "Confidence", value: decision.confidence.formatted(.percent.precision(.fractionLength(1))))
              MetricView(title: "Candidates", value: "\(controller.availableActions.count)")
              MetricView(title: "Latency", value: "\(decision.latencyMilliseconds) ms")
            }.padding(.vertical, 8)
            Text(decision.model).font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
            VStack(alignment: .leading, spacing: 4) {
              ForEach(controller.availableActions) { candidate in
                HStack {
                  Image(systemName: candidate.id == decision.candidate.id ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 11)).foregroundStyle(candidate.id == decision.candidate.id ? PilotTheme.accent : PilotTheme.line)
                    .accessibilityHidden(true)
                  Text(candidate.action.summary).lineLimit(2)
                  Spacer()
                  Text((decision.probabilities[candidate.id] ?? 0).formatted(.percent.precision(.fractionLength(1))))
                    .font(PilotTheme.mono(11)).monospacedDigit()
                }.font(.system(size: 12)).foregroundStyle(candidate.id == decision.candidate.id ? PilotTheme.text : PilotTheme.muted)
                  .padding(12).background(candidate.id == decision.candidate.id ? PilotTheme.inset : .clear, in: RoundedRectangle(cornerRadius: 9))
                  .accessibilityElement(children: .combine)
                  .accessibilityAddTraits(candidate.id == decision.candidate.id ? .isSelected : [])
              }
            }.textSelection(.enabled)
          } else {
            HStack(alignment: .top, spacing: 16) {
              Text("—").font(PilotTheme.mono(24)).foregroundStyle(PilotTheme.muted)
              VStack(alignment: .leading, spacing: 6) {
                Text("No decision yet").font(.system(size: 13)).foregroundStyle(PilotTheme.muted)
              }
            }.padding(.vertical, 16)
          }
        }
        Surface {
          VStack(alignment: .leading, spacing: 14) {
            SectionCaption(title: "Transcription accuracy")
            TextField("Expected phrase", text: $expected).textFieldStyle(.plain).font(PilotTheme.mono(12))
              .padding(12).background(PilotTheme.inset, in: RoundedRectangle(cornerRadius: 8))
              .accessibilityLabel("Expected phrase for transcription comparison")
            if !expected.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !session.transcript.isEmpty {
              let score = TranscriptionAccuracy.compare(reference: expected, transcript: session.transcript)
              HStack {
                MetricView(title: "WER", value: score.wordErrorRate.formatted(.percent.precision(.fractionLength(1))))
                MetricView(title: "Substitutions", value: "\(score.substitutions)")
                MetricView(title: "Insertions", value: "\(score.insertions)")
                MetricView(title: "Deletions", value: "\(score.deletions)")
              }
            } else {
              Text("Compare against your transcript.")
                .font(.system(size: 12)).foregroundStyle(PilotTheme.muted)
            }
          }
        }
      }.padding(30).frame(maxWidth: 980, alignment: .leading).frame(maxWidth: .infinity)
    }.foregroundStyle(PilotTheme.text).background(PilotTheme.background)
  }
  private var headline: String {
    switch session.state {
    case .stopped: "Ready."
    case .preparingModel: "Preparing."
    case .listening: "Listening."
    case .askingJev: "Asking Jev."
    case .running: "Working."
    case .awaitingConfirmation: "Your call."
    case .awaitingAppChoice: "Choose an app."
    case .complete: "Complete."
    case .rejected: "Rejected."
    case .blocked: "Blocked."
    case .error: "Let’s reconnect."
    }
  }
}

/// Editing a shell command sends it back through Jev instead of changing an approved action.
private struct TerminalApprovalView: View {
  @EnvironmentObject private var session: SessionCoordinator
  let command: String
  @State private var draft: String
  init(command: String) { self.command = command; _draft = State(initialValue: command) }
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      TextField("Terminal command", text: $draft).textFieldStyle(.plain)
        .padding(10).background(PilotTheme.inset, in: RoundedRectangle(cornerRadius: 6))
      HStack(spacing: 10) {
        Button(draft == command ? "Approve run" : "Recheck with Jev") {
          if draft == command { session.confirmPendingAction() }
          else { session.revisePendingTerminalCommand(draft) }
        }.buttonStyle(PilotButtonStyle(prominent: true))
          .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        Button("Reject") { session.rejectPendingAction() }.buttonStyle(PilotButtonStyle())
      }
    }
  }
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
