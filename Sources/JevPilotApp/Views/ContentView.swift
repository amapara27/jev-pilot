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
  @EnvironmentObject private var store: RunStore
  @EnvironmentObject private var readiness: Readiness
  @State private var command = ""
  @State private var expected = ""
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
            if !session.transcript.isEmpty {
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
        if let blocker = readiness.probeBlocker {
          HStack(alignment: .center, spacing: 12) {
            Image(systemName: "circle.lefthalf.filled").foregroundStyle(PilotTheme.muted).accessibilityHidden(true)
            Text(blocker).font(.system(size: 12)).foregroundStyle(PilotTheme.muted)
            Spacer(minLength: 8)
            SettingsLink { Text("Set up ↗") }.buttonStyle(PilotButtonStyle())
          }
        }
        if let error = store.errorMessage { Text(error).font(.callout).foregroundStyle(PilotTheme.danger) }
        VStack(alignment: .leading, spacing: 16) {
          SectionCaption(title: "Jev dry-run")
          PilotRule()
          if let result = session.probeResult {
            HStack(alignment: .top) {
              Text(result.decision.candidate.action.summary).font(.system(size: 19, weight: .medium)).textSelection(.enabled)
              Spacer()
              Text("NO ACTION EXECUTED").font(PilotTheme.mono(9)).foregroundStyle(PilotTheme.accent)
            }
            HStack {
              MetricView(title: "Confidence", value: result.decision.confidence.formatted(.percent.precision(.fractionLength(1))))
              MetricView(title: "Candidates", value: "\(result.candidates.count)")
              MetricView(title: "Latency", value: "\(result.decision.latencyMilliseconds) ms")
              MetricView(title: "Model", value: result.decision.model)
            }.padding(.vertical, 8)
            VStack(alignment: .leading, spacing: 8) {
              ForEach(result.candidates) { candidate in
                HStack {
                  Text(candidate.action.summary).lineLimit(2)
                  Spacer()
                  Text((result.decision.probabilities[candidate.id] ?? 0).formatted(.percent.precision(.fractionLength(1))))
                    .font(PilotTheme.mono(11)).monospacedDigit()
                }.font(.system(size: 12)).foregroundStyle(candidate.id == result.decision.candidate.id ? PilotTheme.text : PilotTheme.muted)
              }
            }.textSelection(.enabled)
          } else {
            HStack(alignment: .top, spacing: 16) {
              Text("—").font(PilotTheme.mono(24)).foregroundStyle(PilotTheme.muted)
              VStack(alignment: .leading, spacing: 6) {
                Text("No decision yet").font(.system(size: 13)).foregroundStyle(PilotTheme.muted)
                Text("Speak or type a goal to capture one snapshot and ask Jev once.").font(.system(size: 12)).foregroundStyle(PilotTheme.muted)
              }
            }.padding(.vertical, 16)
          }
        }
        Surface {
          VStack(alignment: .leading, spacing: 14) {
            SectionCaption(title: "Transcription accuracy")
            TextField("Expected phrase", text: $expected).textFieldStyle(.plain).font(PilotTheme.mono(12))
              .padding(12).background(PilotTheme.inset)
            if !expected.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !session.transcript.isEmpty {
              let score = TranscriptionAccuracy.compare(reference: expected, transcript: session.transcript)
              HStack {
                MetricView(title: "WER", value: score.wordErrorRate.formatted(.percent.precision(.fractionLength(1))))
                MetricView(title: "Substitutions", value: "\(score.substitutions)")
                MetricView(title: "Insertions", value: "\(score.insertions)")
                MetricView(title: "Deletions", value: "\(score.deletions)")
              }
            } else {
              Text("Enter what you plan to say, then compare it with the final transcript.")
                .font(.system(size: 12)).foregroundStyle(PilotTheme.muted)
            }
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
                case 1: JSONDebugView(title: "Desktop state", value: session.probeResult?.desktopState)
                case 2: ActionsView(actions: session.probeResult?.candidates ?? [], decision: session.probeResult?.decision)
                default: Text("Probe results are session-only and never enter saved run history.")
                  .font(.callout).foregroundStyle(PilotTheme.muted)
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
    case .preparingModel: "Preparing."
    case .listening: "Listening."
    case .askingJev: "Asking Jev."
    case .complete: "Complete."
    case .error: "Let’s reconnect."
    }
  }
  private func runTyped() {
    session.runTyped(command)
    command = ""
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
