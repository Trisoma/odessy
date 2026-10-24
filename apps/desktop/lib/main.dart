import 'package:flutter/material.dart';

import 'app/app.dart';
import 'app/setup/installed_startup.dart';
import 'app/setup/setup_application.dart';

void main(List<String> arguments) {
  WidgetsFlutterBinding.ensureInitialized();
  final mode = installationMode(arguments);
  final installRoot = launchArgument(arguments, '--install-root');
  final payload = launchArgument(arguments, '--payload');
  if (mode == 'installed') {
    runApp(InstalledApplication(installRoot: installRoot));
    return;
  }
  runApp(mode == null
      ? const OdessyApp()
      : SetupApplication(
          mode: mode, payloadRoot: payload, installRoot: installRoot));
}

String launchArgument(List<String> arguments, String name) {
  final index = arguments.indexOf(name);
  if (index >= 0 && index + 1 < arguments.length) return arguments[index + 1];
  for (final argument in arguments) {
    if (argument.startsWith('$name=')) return argument.substring(name.length + 1);
  }
  return '';
}

