import 'dart:io';

import 'setup_application.dart' show installationMode;

/// The payload shipped next to the executable, or an empty string.
///
/// A distributed setup folder keeps `payload/` beside the binary, so the installer
/// needs no internal path. An installed copy has no such sibling, so this only
/// changes anything for the installer itself.
String discoverPayloadRoot({String? executablePath}) {
  final executable = executablePath ?? Platform.resolvedExecutable;
  final directory = File(executable).parent.path;
  final candidate = '$directory${Platform.pathSeparator}payload';
  final marker = '$candidate${Platform.pathSeparator}setup-context.json';
  if (!File(marker).existsSync()) return '';
  return candidate;
}

/// [arguments] with the packaged payload and setup mode filled in.
///
/// Running the packaged binary with no arguments (a double-click, or `./OdessySetup`)
/// has to install rather than open the application, otherwise the entry point is not
/// discoverable for a normal user.
List<String> resolveLaunchArguments(
  List<String> arguments, {
  String? executablePath,
}) {
  final payload = discoverPayloadRoot(executablePath: executablePath);
  if (payload.isEmpty) return arguments;
  final resolved = [...arguments];
  final hasPayload = resolved.any((argument) =>
      argument == '--payload' || argument.startsWith('--payload='));
  if (!hasPayload) resolved.addAll(['--payload', payload]);
  if (installationMode(resolved) == null) resolved.add('--setup');
  return resolved;
}
