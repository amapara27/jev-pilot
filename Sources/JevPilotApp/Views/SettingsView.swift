// A compact preferences sheet uses the same tokens and controls as the command workspace.
import AppKit
import JevPilotCore
import SwiftUI

struct SettingsView: View {
  @EnvironmentObject private var session: SessionCoordinator
  @EnvironmentObject private var store: RunStore
  @EnvironmentObject private var readiness: Readiness
  @Environment(\.scenePhase) private var phase
  @AppStorage("inputPrice") private var inputPrice = 0.042
  @AppStorage("outputPrice") private var outputPrice = 0.0
  @State private var inputDraft = ""
  @State private var outputDraft = ""
  @State private var apiKey = ""
  @State private var message = ""
  @State private var pricingMessage = ""
  @State private var clearConfirmation = false
  private let keyStore = KeychainAPIKeyStore()
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 28) {
        HStack(alignment: .firstTextBaseline) {
          Text("Preferences").font(.system(size: 29, weight: .medium)).tracking(-0.8)
          Spacer()
        }
        SettingsSection(title: "01 / Connection") {
          HStack {
            Text("TypeSafe / Jev").font(.system(size: 14, weight: .medium))
            Spacer()
            Text(readiness.hasKey ? "CONFIGURED" : "NOT CONFIGURED").font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
          }
          SecureField("API key", text: $apiKey).textFieldStyle(.plain)
            .padding(12).background(PilotTheme.surface).overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(PilotTheme.line))
          HStack {
            Button("Save to Keychain", action: save).buttonStyle(PilotButtonStyle(prominent: true))
              .disabled(apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            Button("Remove saved key", role: .destructive, action: remove).buttonStyle(PilotButtonStyle())
          }
          note("Stored in Keychain. Connects directly to TypeSafe.")
          if !message.isEmpty { Text(message).font(.caption) }
        }
        SettingsSection(title: "02 / Permissions") {
          permissionRow("Accessibility", status: readiness.accessibility ? "Allowed" : "Required", pane: "Privacy_Accessibility")
          permissionRow("Microphone", status: readiness.microphone == .authorized ? "Allowed" : readiness.microphone == .notDetermined ? "Not requested" : "Denied", pane: "Privacy_Microphone")
          Button("Refresh status") { readiness.refresh() }.buttonStyle(PilotButtonStyle())
        }
        SettingsSection(title: "03 / Voice") {
          ListeningModeSelector().frame(width: 260)
          Picker("Parakeet EOU model", selection: $session.speechPreset) {
            ForEach(SpeechRecognitionPreset.allCases) { preset in Text(preset.title).tag(preset) }
          }.frame(width: 300).disabled(session.state.isActive)
          HStack {
            Button("Prepare Model") { session.prepareSpeechModel() }
              .buttonStyle(PilotButtonStyle(prominent: true)).disabled(session.state.isActive)
            Text(modelStatus).font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
          }
          Toggle("Show live transcript overlay", isOn: $session.showTranscript).toggleStyle(.checkbox).font(.system(size: 13))
          note("English-only Parakeet EOU. Models are cached under ~/Library/Application Support/FluidAudio; cached transcription stays local and offline.")
        }
        SettingsSection(title: "04 / Estimated pricing", trailing: "USD / MILLION TOKENS") {
          HStack(spacing: 20) {
            rateField("Input tokens", text: $inputDraft)
            rateField("Output tokens", text: $outputDraft)
          }
          Button("Save pricing", action: savePricing).buttonStyle(PilotButtonStyle())
          if !pricingMessage.isEmpty { Text(pricingMessage).font(.caption) }
          note("Applies to new runs only.")
          Link("TypeSafe pricing reference ↗", destination: URL(string: "https://typesafe.ai/blog/introducing-system-one-models-and-jev")!)
            .font(.system(size: 12)).foregroundStyle(PilotTheme.accent)
        }
        SettingsSection(title: "05 / Local history") {
          note("100 runs, stored locally. Includes command text, never audio.")
          if let error = store.errorMessage { Text(error).font(.caption).foregroundStyle(PilotTheme.danger) }
          Button("Clear history…", role: .destructive) { clearConfirmation = true }.buttonStyle(PilotButtonStyle(destructive: true))
        }
      }.padding(30)
    }.frame(width: 610, height: 690).foregroundStyle(PilotTheme.text).background(PilotTheme.background).tint(PilotTheme.accent)
      .onAppear {
        readiness.refresh()
        inputDraft = String(inputPrice)
        outputDraft = String(outputPrice)
      }
      .onChange(of: phase) { _, phase in if phase == .active { readiness.refresh() } }
      .alert("Clear saved history?", isPresented: $clearConfirmation) {
        Button("Cancel", role: .cancel) {}
        Button("Clear history", role: .destructive) { store.clear() }
      } message: { Text("Saved runs and their usage totals will be removed. An active run will be kept.") }
  }
  private func note(_ text: String) -> some View {
    Text(text).font(.system(size: 12)).foregroundStyle(PilotTheme.muted).lineSpacing(3).fixedSize(horizontal: false, vertical: true)
  }
  private var modelStatus: String {
    if case .preparingModel = session.state { return session.state.label.uppercased() }
    return session.speechPreset.isCached ? "READY IN LOCAL CACHE" : "DOWNLOAD REQUIRED"
  }
  private func rateField(_ title: String, text: Binding<String>) -> some View {
    VStack(alignment: .leading, spacing: 8) {
      Text(title).font(.system(size: 12)).foregroundStyle(PilotTheme.muted)
      TextField(title, text: text).font(PilotTheme.mono(14)).textFieldStyle(.plain)
        .padding(12).background(PilotTheme.surface).overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(PilotTheme.line))
    }
  }
  private func permissionRow(_ title: String, status: String, pane: String) -> some View {
    HStack {
      Text(title).font(.system(size: 13))
      Spacer()
      Text(status).font(PilotTheme.mono(10)).foregroundStyle(PilotTheme.muted)
      Button("Open ↗") { readiness.openPermission(pane) }.buttonStyle(PilotButtonStyle()).help("Open \(title) permissions")
        .accessibilityLabel("Open \(title) permissions")
    }
  }
  private func savePricing() {
    guard let input = Double(inputDraft), let output = Double(outputDraft), input.isFinite, output.isFinite, input >= 0, output >= 0 else {
      pricingMessage = "Enter a nonnegative number for each rate."
      return
    }
    inputPrice = input
    outputPrice = output
    pricingMessage = "Saved for future runs."
  }
  private func save() {
    do {
      try keyStore.save(apiKey.trimmingCharacters(in: .whitespacesAndNewlines))
      apiKey = ""
      message = "Saved securely in Keychain."
    } catch { message = error.localizedDescription }
    readiness.refresh()
  }
  private func remove() {
    do {
      try keyStore.delete()
      apiKey = ""
      message = "Saved key removed. An environment key may still be available."
    } catch { message = error.localizedDescription }
    readiness.refresh()
  }
}

/// Flat sections provide grouping without system grouped-form cards.
private struct SettingsSection<Content: View>: View {
  let title: String
  var trailing = ""
  @ViewBuilder var content: Content
  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      SectionCaption(title: title, trailing: trailing)
      PilotRule()
      content
    }
  }
}
