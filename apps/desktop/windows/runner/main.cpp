#include <flutter/dart_project.h>
#include <flutter/flutter_view_controller.h>
#include <flutter_windows.h>
#include <windows.h>
#include <algorithm>
#include <cwctype>
#include <sstream>
#include <string>
#include <vector>

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

// Setup, manage and uninstall are one installer surface and share a compact
// window; the installed application keeps the larger productivity layout.
// Sizes are logical pixels and are scaled for the monitor DPI by
// Win32Window::Create.
constexpr int kCompactWidth = 900;
constexpr int kCompactHeight = 680;
constexpr int kCompactMinWidth = 720;
constexpr int kCompactMinHeight = 540;
constexpr int kMainWidth = 1200;
constexpr int kMainHeight = 850;

enum class LaunchMode { kApplication, kSetup, kManage, kUninstall, kInstalled };

bool HasArgument(const std::vector<std::string>& arguments, const char* flag) {
  return std::find(arguments.begin(), arguments.end(), flag) !=
         arguments.end();
}

// Mirrors installationMode() in lib/app/setup/setup_application.dart so the
// native window and the Flutter surface read the same flag precedence.
LaunchMode LaunchModeFor(const std::vector<std::string>& arguments) {
  if (HasArgument(arguments, "--installed")) return LaunchMode::kInstalled;
  if (HasArgument(arguments, "--uninstall")) return LaunchMode::kUninstall;
  if (HasArgument(arguments, "--setup")) return LaunchMode::kSetup;
  if (HasArgument(arguments, "--manage")) return LaunchMode::kManage;
  return LaunchMode::kApplication;
}

bool IsSetupSurface(LaunchMode mode) {
  return mode == LaunchMode::kSetup || mode == LaunchMode::kManage ||
         mode == LaunchMode::kUninstall;
}

// A compact window that is taller than the work area would open with its
// footer below the screen edge. Fit the initial size to the primary monitor's
// work area in logical pixels; the user may still resize down to the minimum.
Win32Window::Size FitToWorkArea(const Win32Window::Size& size) {
  POINT origin{0, 0};
  HMONITOR monitor = MonitorFromPoint(origin, MONITOR_DEFAULTTOPRIMARY);
  MONITORINFO info{};
  info.cbSize = sizeof(info);
  if (!GetMonitorInfoW(monitor, &info)) return size;
  const double scale_factor = FlutterDesktopGetDpiForMonitor(monitor) / 96.0;
  if (scale_factor <= 0) return size;
  constexpr int kMargin = 24;
  const int available_width = std::max(
      320, static_cast<int>((info.rcWork.right - info.rcWork.left) /
                            scale_factor) -
               kMargin);
  const int available_height = std::max(
      240, static_cast<int>((info.rcWork.bottom - info.rcWork.top) /
                            scale_factor) -
               kMargin);
  return Win32Window::Size(
      std::min(static_cast<int>(size.width), available_width),
      std::min(static_cast<int>(size.height), available_height));
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
  const LaunchMode mode = LaunchModeFor(arguments);
  Win32Window::Size size(kMainWidth, kMainHeight);
  Win32Window::Size minimum(0, 0);
  std::wstring title = L"Odessy";
  if (IsSetupSurface(mode)) {
    title = mode == LaunchMode::kSetup
                ? L"Odessy Setup"
                : mode == LaunchMode::kManage ? L"Manage Odessy"
                                              : L"Uninstall Odessy";
    size = FitToWorkArea(Win32Window::Size(kCompactWidth, kCompactHeight));
    // The minimum never exceeds the size that actually fits the screen, so a
    // small or high-DPI display can still shrink the window to its footer.
    minimum = Win32Window::Size(std::min<unsigned int>(kCompactMinWidth,
                                                       size.width),
                                std::min<unsigned int>(kCompactMinHeight,
                                                       size.height));
  }
  if (!window.Create(title, origin, size)) {
    if (instance_mutex) CloseHandle(instance_mutex);
    return EXIT_FAILURE;
  }
  window.SetMinimumSize(minimum);
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
