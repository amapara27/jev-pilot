import JevPilotCore
import SwiftUI

struct ContentView: View {
  @ObservedObject var controller: AutomationController
  @ObservedObject var speechRecognizer: LocalSpeechRecognizer
  @State private var command = ""
  @State private var selectedPanel: DebugPanel = .timeline

  var body: some View {
    NavigationSplitView {
      VStack(alignment: .leading, spacing: 18) {
        VStack(alignment: .leading, spacing: 6) {
          Label("Jev Pilot", systemImage: "waveform.and.mic")
            .font(.title.bold())
          Text("Bounded, auditable desktop control")
            .foregroundStyle(.secondary)
        }

        commandComposer

        Divider()

        statusCard

        Picker("Debug panel", selection: $selectedPanel) {
          ForEach(DebugPanel.allCases) { panel in
            Label(panel.title, systemImage: panel.icon).tag(panel)
          }
        }
        .pickerStyle(.inline)

        Spacer()

        HStack {
          Button("Accessibility…") { controller.requestAccessibilityPermission() }
          SettingsLink { Image(systemName: "gearshape") }
        }
      }
      .padding()
      .navigationSplitViewColumnWidth(min: 280, ideal: 320)
    } detail: {
      Group {
        switch selectedPanel {
        case .timeline:
          TimelineView(events: controller.debugEvents)
        case .state:
          JSONDebugView(title: "Desktop state", value: controller.latestState)
        case .actions:
          ActionsView(actions: controller.availableActions, decision: controller.latestDecision)
        }
      }
      .navigationTitle(selectedPanel.title)
    }
    .onChange(of: speechRecognizer.transcript) { _, newValue in
      if speechRecognizer.isRecording || !newValue.isEmpty { command = newValue }
    }
    .alert(
      "Confirm action", isPresented: confirmationBinding, presenting: controller.pendingConfirmation
    ) { _ in
      Button("Cancel", role: .cancel) { controller.rejectPendingAction() }
      Button("Run action") { controller.confirmPendingAction() }
    } message: { pending in
      Text("\(pending.decision.candidate.action.summary)\n\n\(pending.assessment.reason)")
    }
  }

  private var commandComposer: some View {
    VStack(alignment: .leading, spacing: 8) {
      Text("Command").font(.headline)
      TextField("Try “switch back to Terminal”", text: $command, axis: .vertical)
        .lineLimit(3...6)
        .textFieldStyle(.roundedBorder)
        .onSubmit(run)
      HStack {
        Button(action: toggleRecording) {
          Label(
            speechRecognizer.isRecording ? "Stop listening" : "Speak",
            systemImage: speechRecognizer.isRecording ? "stop.circle.fill" : "mic.fill"
          )
        }
        Button("Run", action: run)
          .buttonStyle(.borderedProminent)
          .disabled(command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        if case .running = controller.status {
          Button("Stop", role: .destructive) { controller.cancel() }
        }
      }
      if let error = speechRecognizer.errorMessage {
        Text(error).font(.caption).foregroundStyle(.red)
      }
    }
  }

  private var statusCard: some View {
    VStack(alignment: .leading, spacing: 8) {
      Text("Status").font(.headline)
      Text(controller.status.label)
        .foregroundStyle(statusColor)
      if let decision = controller.latestDecision {
        Text(decision.candidate.action.summary)
          .font(.callout.weight(.medium))
        ProgressView(value: decision.confidence) {
          Text("Jev confidence")
        } currentValueLabel: {
          Text(decision.confidence, format: .percent.precision(.fractionLength(1)))
        }
      }
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
  }

  private var confirmationBinding: Binding<Bool> {
    Binding(
      get: { controller.pendingConfirmation != nil },
      set: { if !$0, controller.pendingConfirmation != nil { controller.rejectPendingAction() } }
    )
  }

  private var statusColor: Color {
    if case .failed = controller.status { return .red }
    if case .completed = controller.status { return .green }
    return .secondary
  }

  private func run() {
    speechRecognizer.stop()
    controller.run(goal: command)
  }

  private func toggleRecording() {
    if speechRecognizer.isRecording {
      speechRecognizer.stop()
    } else {
      Task { await speechRecognizer.start() }
    }
  }
}

private enum DebugPanel: String, CaseIterable, Identifiable {
  case timeline
  case state
  case actions

  var id: String { rawValue }
  var title: String { rawValue.capitalized }
  var icon: String {
    switch self {
    case .timeline: "list.bullet.rectangle"
    case .state: "macwindow"
    case .actions: "cursorarrow.click.2"
    }
  }
}
