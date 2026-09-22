// Keeps the few durable app preferences in one compact, progressively disclosed surface.
import AppKit
import JevPilotCore
import SwiftUI

struct SettingsView: View {
  @EnvironmentObject private var session: SessionCoordinator
  @EnvironmentObject private var readiness: Readiness
  @Environment(\.scenePhase) private var phase
  @State private var apiKeyDraft = ""
  @State private var keyError = ""
  @State private var isEditingKey = false
  @State private var removeKeyConfirmation = false
  private let keyStore = KeychainAPIKeyStore()

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 22) {
        Text("Settings")
          .font(.system(size: 26, weight: .medium))
          .tracking(-0.6)

        SettingsSection(title: "Connection") {
          HStack(spacing: 10) {
            Text("TypeSafe API key").font(.system(size: 13, weight: .medium))
            Spacer()
            if readiness.hasKey {
              Button("Change API key") { openKeyEditor() }
                .buttonStyle(PilotButtonStyle())
              if readiness.hasStoredKey {
                Button("Remove", role: .destructive) { removeKeyConfirmation = true }
                  .buttonStyle(PilotButtonStyle(destructive: true))
              }
            } else {
              Button("Add API key") { openKeyEditor() }
                .buttonStyle(PilotButtonStyle(prominent: true))
            }
          }
          if !keyError.isEmpty {
            Text(keyError).font(.caption).foregroundStyle(PilotTheme.danger)
          }
        }

        SettingsSection(title: "Voice") {
          HStack {
            Text("Model").font(.system(size: 13))
            Spacer()
            Picker("Voice model", selection: $session.speechPreset) {
              ForEach(SpeechRecognitionPreset.allCases) { preset in
                Text(preset.title).tag(preset)
              }
            }
            .labelsHidden()
            .frame(width: 190)
            .disabled(session.state.isActive)
          }
          Toggle("Live transcript overlay", isOn: $session.showTranscript)
            .toggleStyle(.checkbox)
            .font(.system(size: 13))
        }

        SettingsSection(title: "Permissions") {
          HStack(spacing: 16) {
            PermissionStatus(title: "Accessibility", allowed: readiness.accessibility)
            PermissionStatus(title: "Microphone", allowed: readiness.microphone == .authorized)
            Spacer()
            Menu("Manage…") {
              Button("Accessibility…") { readiness.openPermission("Privacy_Accessibility") }
              Button("Microphone…") { readiness.openPermission("Privacy_Microphone") }
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .accessibilityLabel("Manage permissions")
          }
        }
      }
      .padding(24)
    }
    .frame(width: 470, height: 390)
    .foregroundStyle(PilotTheme.text)
    .background(PilotTheme.background)
    .tint(PilotTheme.accent)
    .onAppear { readiness.refresh() }
    .onChange(of: phase) { _, phase in
      if phase == .active { readiness.refresh() }
    }
    .sheet(isPresented: $isEditingKey) {
      APIKeyEditor(
        title: readiness.hasKey ? "Change API key" : "Add API key",
        key: $apiKeyDraft,
        error: $keyError,
        cancel: { isEditingKey = false },
        save: saveKey
      )
    }
    .alert("Remove API key?", isPresented: $removeKeyConfirmation) {
      Button("Cancel", role: .cancel) {}
      Button("Remove", role: .destructive, action: removeKey)
    } message: {
      Text("The saved key will be removed from Keychain.")
    }
  }

  private func openKeyEditor() {
    apiKeyDraft = ""
    keyError = ""
    isEditingKey = true
  }

  private func saveKey() {
    let key = apiKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !key.isEmpty else { return }
    do {
      try keyStore.save(key)
      apiKeyDraft = ""
      keyError = ""
      isEditingKey = false
      readiness.refresh()
    } catch {
      keyError = error.localizedDescription
    }
  }

  private func removeKey() {
    do {
      try keyStore.delete()
      keyError = ""
      readiness.refresh()
    } catch {
      keyError = error.localizedDescription
    }
  }
}

/// A key field appears only after the user explicitly chooses Add or Change.
private struct APIKeyEditor: View {
  let title: String
  @Binding var key: String
  @Binding var error: String
  let cancel: () -> Void
  let save: () -> Void
  @FocusState private var isFocused: Bool

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      Text(title).font(.system(size: 20, weight: .medium))
      SecureField("API key", text: $key)
        .textFieldStyle(.plain)
        .padding(12)
        .background(PilotTheme.surface)
        .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(PilotTheme.line))
        .focused($isFocused)
        .onSubmit(save)
      if !error.isEmpty {
        Text(error).font(.caption).foregroundStyle(PilotTheme.danger)
      }
      HStack {
        Spacer()
        Button("Cancel", action: cancel).buttonStyle(PilotButtonStyle())
        Button("Save", action: save)
          .buttonStyle(PilotButtonStyle(prominent: true))
          .disabled(key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
      }
    }
    .padding(24)
    .frame(width: 370)
    .foregroundStyle(PilotTheme.text)
    .background(PilotTheme.background)
    .onAppear { isFocused = true }
  }
}

/// Permission state is visible without expanding settings into setup instructions.
private struct PermissionStatus: View {
  let title: String
  let allowed: Bool

  var body: some View {
    Label(title, systemImage: allowed ? "checkmark.circle.fill" : "circle")
      .font(.system(size: 12))
      .foregroundStyle(allowed ? PilotTheme.text : PilotTheme.muted)
      .accessibilityLabel("\(title): \(allowed ? "allowed" : "needed")")
  }
}

/// Flat sections retain hierarchy without captions, numbering, or nested card chrome.
private struct SettingsSection<Content: View>: View {
  let title: String
  @ViewBuilder var content: Content

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      Text(title).font(.system(size: 13, weight: .semibold))
      PilotRule()
      content
    }
  }
}
