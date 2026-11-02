#include "credential_vault.h"

#include <windows.h>
#include <wincred.h>
#include <flutter/standard_method_codec.h>
#include <string>
#include <variant>

namespace {
std::wstring Wide(const std::string& input) {
  if (input.empty() || input.size() > 1024 || input.find('\0') != std::string::npos) return {};
  int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(),
                                 static_cast<int>(input.size()), nullptr, 0);
  if (!size) return {};
  std::wstring output(size, L'\0');
  if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(),
                           static_cast<int>(input.size()), output.data(), size)) return {};
  return output;
}
}

std::unique_ptr<flutter::MethodChannel<flutter::EncodableValue>>
CreateCredentialVault(flutter::BinaryMessenger* messenger) {
  auto channel = std::make_unique<flutter::MethodChannel<flutter::EncodableValue>>(
      messenger, "odessy/credential_vault", &flutter::StandardMethodCodec::GetInstance());
  channel->SetMethodCallHandler([](const auto& call, auto result) {
    const auto* args = std::get_if<flutter::EncodableMap>(call.arguments());
    if (!args) { result->Error("invalid", "Invalid credential request"); return; }
    auto origin_entry = args->find(flutter::EncodableValue("origin"));
    const auto* origin = origin_entry == args->end() ? nullptr :
        std::get_if<std::string>(&origin_entry->second);
    if (!origin) { result->Error("invalid", "Invalid credential origin"); return; }
    auto wide = Wide(*origin);
    if (wide.empty()) { result->Error("invalid", "Invalid credential origin"); return; }
    const std::wstring target = L"Odessy/Desktop/" + wide;
    if (call.method_name() == "read") {
      PCREDENTIALW credential = nullptr;
      if (!CredReadW(target.c_str(), CRED_TYPE_GENERIC, 0, &credential)) {
        if (GetLastError() == ERROR_NOT_FOUND) result->Success();
        else result->Error("vault", "Credential storage is unavailable");
        return;
      }
      if (!credential->CredentialBlob || !credential->CredentialBlobSize ||
          credential->CredentialBlobSize > CRED_MAX_CREDENTIAL_BLOB_SIZE ||
          !MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
              reinterpret_cast<char*>(credential->CredentialBlob),
              static_cast<int>(credential->CredentialBlobSize), nullptr, 0)) {
        if (credential->CredentialBlob && credential->CredentialBlobSize)
          SecureZeroMemory(credential->CredentialBlob, credential->CredentialBlobSize);
        CredFree(credential);
        result->Error("vault", "Stored credential is invalid");
        return;
      }
      std::string token(reinterpret_cast<char*>(credential->CredentialBlob), credential->CredentialBlobSize);
      if (credential->CredentialBlobSize) SecureZeroMemory(credential->CredentialBlob, credential->CredentialBlobSize);
      CredFree(credential);
      result->Success(flutter::EncodableValue(token));
      if (!token.empty()) SecureZeroMemory(token.data(), token.size());
    } else if (call.method_name() == "write") {
      auto entry = args->find(flutter::EncodableValue("token"));
      const auto* token = entry == args->end() ? nullptr : std::get_if<std::string>(&entry->second);
      if (!token || token->empty() || token->size() > CRED_MAX_CREDENTIAL_BLOB_SIZE ||
          token->find('\0') != std::string::npos) {
        result->Error("invalid", "Invalid credential"); return;
      }
      CREDENTIALW credential{};
      credential.Type = CRED_TYPE_GENERIC;
      credential.TargetName = const_cast<wchar_t*>(target.c_str());
      credential.CredentialBlobSize = static_cast<DWORD>(token->size());
      credential.CredentialBlob = reinterpret_cast<LPBYTE>(const_cast<char*>(token->data()));
      credential.Persist = CRED_PERSIST_LOCAL_MACHINE;
      if (!CredWriteW(&credential, 0)) result->Error("vault", "Credential storage is unavailable");
      else result->Success();
    } else if (call.method_name() == "remove") {
      if (!CredDeleteW(target.c_str(), CRED_TYPE_GENERIC, 0) && GetLastError() != ERROR_NOT_FOUND)
        result->Error("vault", "Credential storage is unavailable");
      else result->Success();
    } else {
      result->NotImplemented();
    }
  });
  return channel;
}
