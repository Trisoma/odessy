import 'dart:io';

/// True when this process is not part of the installation it manages.
///
/// Uninstall requires it: a running installation cannot remove the files it is
/// executing from, so removal is delegated to the external maintenance copy.
bool runningOutsideInstallation(String root) {
  if (root.isEmpty) return true;
  final executable =
      Platform.resolvedExecutable.toLowerCase().replaceAll('\\', '/');
  final installation = root.toLowerCase().replaceAll('\\', '/');
  return !executable.startsWith(installation);
}
