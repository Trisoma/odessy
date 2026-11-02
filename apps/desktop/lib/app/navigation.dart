import 'package:flutter/material.dart';

/// Presentation routes only. Routing never changes service, workspace or tool authority.
class DesktopDestination {
  const DesktopDestination(this.route, this.label, this.icon);
  final String route, label;
  final IconData icon;
}

abstract final class DesktopNavigation {
  static const primary = [
    DesktopDestination('Conversation', 'Assistant', Icons.chat_bubble_outline),
    DesktopDestination('Workspaces', 'Workspaces', Icons.folder_open_outlined),
    DesktopDestination('Devices', 'Devices', Icons.devices_outlined),
  ];
  static const settings =
      DesktopDestination('Settings', 'Settings', Icons.tune);
  static String parent(String route) => switch (route) {
        'Monitor' ||
        'Pairing' ||
        'Local pairing' ||
        'Device details' =>
          'Devices',
        'Installer' ||
        'Maintenance' ||
        'Manage installation' ||
        'Get started' ||
        'Voice setup' ||
        'Setup' =>
          'Settings',
        _ => route,
      };
  static String title(String route) => switch (route) {
        'Conversation' => 'Assistant',
        'Monitor' => 'Devices',
        'Installer' => 'Install Odessy',
        'Maintenance' => 'Installation & recovery',
        'Pairing' => 'Connect a device',
        'Local pairing' => 'Local pairing tools',
        'Setup' => 'Computer configuration',
        _ => route,
      };
}
