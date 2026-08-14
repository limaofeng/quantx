import SwiftUI

struct LoginView: View {
  @EnvironmentObject private var model: AppModel
  @State private var username = ""
  @State private var password = ""

  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(alignment: .leading, spacing: 22) {
          Image(systemName: "lock.shield.fill")
            .font(.system(size: 40))
            .foregroundStyle(QuantXTheme.accent)
            .accessibilityHidden(true)

          VStack(alignment: .leading, spacing: 6) {
            Text("登录 QuantX")
              .font(.largeTitle.bold())
            Text(loginDescription)
              .font(.subheadline)
              .foregroundStyle(QuantXTheme.secondaryText)
          }

          VStack(spacing: 14) {
            TextField("用户名", text: $username)
              .textContentType(.username)
              .textInputAutocapitalization(.never)
              .autocorrectionDisabled()
              .submitLabel(.next)
              .textFieldStyle(.roundedBorder)

            SecureField("密码", text: $password)
              .textContentType(.password)
              .submitLabel(.go)
              .textFieldStyle(.roundedBorder)
              .onSubmit(login)
          }

          if let message = model.authenticationErrorMessage {
            Label(message, systemImage: "exclamationmark.triangle.fill")
              .font(.footnote)
              .foregroundStyle(QuantXTheme.warning)
              .accessibilityElement(children: .combine)
          }

          Button(action: login) {
            HStack {
              if model.authenticationIsBusy {
                ProgressView()
                  .tint(.white)
              }
              Text(model.authenticationIsBusy ? "正在验证…" : "登录并加载数据")
                .fontWeight(.semibold)
            }
            .frame(maxWidth: .infinity, minHeight: 48)
          }
          .buttonStyle(.borderedProminent)
          .disabled(
            username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
              || password.isEmpty
              || model.authenticationIsBusy
          )

          Label(sessionNotice, systemImage: noticeSymbol)
            .font(.footnote)
            .foregroundStyle(
              usesInsecureDevelopmentTransport
                ? QuantXTheme.warning
                : QuantXTheme.secondaryText
            )
        }
        .padding(24)
      }
      .background(QuantXTheme.canvasBackground)
    }
  }

  private var usesInsecureDevelopmentTransport: Bool {
    model.configuration?.environment == .debug
      && model.configuration?.usesInsecureAccountTransport == true
  }

  private var loginDescription: String {
    if usesInsecureDevelopmentTransport {
      return "开发环境将通过局域网加载真实行情、账户与量化状态。"
    }
    return "登录后加载已授权的真实行情、账户与量化能力。"
  }

  private var sessionNotice: String {
    if usesInsecureDevelopmentTransport {
      return "当前使用开发环境 HTTP 明文传输；密码不落盘，会话令牌仍仅存本机 Keychain。"
    }
    return "密码不会写入磁盘；会话令牌仅存储在本机 Keychain。"
  }

  private var noticeSymbol: String {
    usesInsecureDevelopmentTransport ? "exclamationmark.shield.fill" : "key.fill"
  }

  private func login() {
    let submittedUsername = username.trimmingCharacters(in: .whitespacesAndNewlines)
    let submittedPassword = password
    password = ""
    Task {
      await model.login(username: submittedUsername, password: submittedPassword)
    }
  }
}
