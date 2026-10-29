#include <flutter/dart_project.h>
#include <flutter/flutter_view_controller.h>
#include <windows.h>
#include <cwctype>
#include <sstream>

#include "flutter_window.h"
#include "utils.h"

namespace {
struct Activation { std::wstring property; HWND window = nullptr; };
BOOL CALLBACK FindInstance(HWND window, LPARAM data) {
  auto* activation = reinterpret_cast<Activation*>(data);
  if (GetPropW(window, activation->property.c_str())) {
    activation->window = window;
    return FALSE;
  }
  return TRUE;
}
std::wstring Wide(const std::string& value) {
  int size = MultiByteToWideChar(CP_UTF8, 0, value.c_str(), -1, nullptr, 0);
  if (size <= 0) return L"";
  std::wstring result(size, L'\0');
  MultiByteToWideChar(CP_UTF8, 0, value.c_str(), -1, result.data(), size);
  result.pop_back();
  return result;
}
std::wstring InstanceKey(const std::vector<std::string>& arguments) {
  std::wstring mode, root;
  for (size_t i = 0; i < arguments.size(); ++i) {
    const auto& arg = arguments[i];
    if (arg == "--installed" || arg == "--setup" || arg == "--manage" || arg == "--uninstall") mode = Wide(arg);
    if (arg == "--install-root" && i + 1 < arguments.size()) root = Wide(arguments[i + 1]);
    if (arg.rfind("--install-root=", 0) == 0) root = Wide(arg.substr(15));
  }
  if (mode.empty()) return L"";
  wchar_t local[32768] = {};
  GetEnvironmentVariableW(L"LOCALAPPDATA", local, 32768);
  if (root.empty()) root = std::wstring(local) + L"\\Odessy\\App";
  wchar_t full[32768] = {};
  DWORD length = GetFullPathNameW(root.c_str(), 32768, full, nullptr);
  if (length > 0 && length < 32768) root = full;
  while (root.size() > 3 && (root.back() == L'\\' || root.back() == L'/')) root.pop_back();
  auto value = std::wstring(local) + L"|" + mode + L"|" + root;
  unsigned long long hash = 14695981039346656037ULL;
  for (wchar_t ch : value) {
    if (ch == L'/') ch = L'\\';
    hash ^= static_cast<unsigned long long>(std::towlower(ch));
    hash *= 1099511628211ULL;
  }
  std::wostringstream output;
  output << L"Odessy.Instance." << std::hex << hash;
  return output.str();
}
}  // namespace

int APIENTRY wWinMain(_In_ HINSTANCE instance, _In_opt_ HINSTANCE prev,
                      _In_ wchar_t *command_line, _In_ int show_command) {
  auto arguments = GetCommandLineArguments();
  auto key = InstanceKey(arguments);
  HANDLE instance_mutex = nullptr;
  if (!key.empty()) {
    instance_mutex = CreateMutexW(nullptr, FALSE, (L"Local\\" + key).c_str());
    if (!instance_mutex) return EXIT_FAILURE;
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
      Activation activation{key};
      for (int attempt = 0; attempt < 100 && !activation.window; ++attempt) {
        EnumWindows(FindInstance, reinterpret_cast<LPARAM>(&activation));
        if (!activation.window) Sleep(25);
      }
      if (activation.window) {
        ShowWindow(activation.window, SW_RESTORE);
        BringWindowToTop(activation.window);
        SetForegroundWindow(activation.window);
      }
      CloseHandle(instance_mutex);
      return activation.window ? EXIT_SUCCESS : EXIT_FAILURE;
    }
  }
  // Attach to console when present (e.g., 'flutter run') or create a
  // new console when running with a debugger.
  if (!::AttachConsole(ATTACH_PARENT_PROCESS) && ::IsDebuggerPresent()) {
    CreateAndAttachConsole();
  }

  // Initialize COM, so that it is available for use in the library and/or
  // plugins.
  ::CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

  flutter::DartProject project(L"data");

  std::vector<std::string> command_line_arguments =
      GetCommandLineArguments();

  project.set_dart_entrypoint_arguments(std::move(command_line_arguments));

  FlutterWindow window(project);
  Win32Window::Point origin(10, 10);
  Win32Window::Size size(1200, 850);
  if (!window.Create(L"Odessy", origin, size)) {
    if (instance_mutex) CloseHandle(instance_mutex);
    return EXIT_FAILURE;
  }
  if (!key.empty()) SetPropW(window.GetHandle(), key.c_str(), reinterpret_cast<HANDLE>(1));
  window.SetQuitOnClose(true);

  ::MSG msg;
  while (::GetMessage(&msg, nullptr, 0, 0)) {
    ::TranslateMessage(&msg);
    ::DispatchMessage(&msg);
  }

  ::CoUninitialize();
  if (instance_mutex) CloseHandle(instance_mutex);
  return EXIT_SUCCESS;
}
