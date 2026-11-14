import 'package:flutter/material.dart';
import '../../../widgets/pixel_boat.dart';
import 'device_sidebar.dart';

/// App navigation only. Projects and chats live in the adjacent sidebar.
class AppIconRail extends StatelessWidget {
  const AppIconRail(
      {super.key,
      required this.destinations,
      required this.onSettings,
      this.settingsSelected = false,
      this.navigationFocus});
  final List<SidebarDestination> destinations;
  final VoidCallback onSettings;
  final bool settingsSelected;
  final Map<String, FocusNode>? navigationFocus;

  Widget _button(BuildContext context, SidebarDestination item) {
    final focus = navigationFocus?.putIfAbsent(
        item.label, () => FocusNode(debugLabel: 'Navigation: ${item.label}'));
    return Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: IconButton(
            key: ValueKey('nav-${item.label}'),
            focusNode: focus,
            tooltip: item.label == 'Assistant' ? 'Chat' : item.label,
            isSelected: item.selected,
            style: IconButton.styleFrom(
                backgroundColor: item.selected
                    ? Theme.of(context).colorScheme.secondaryContainer
                    : null,
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10)),
                minimumSize: const Size(44, 44)),
            onPressed: item.onTap == null
                ? null
                : () {
                    focus?.requestFocus();
                    item.onTap!();
                  },
            icon: Icon(item.icon, size: 21)));
  }

  @override
  Widget build(BuildContext context) => SizedBox(
      key: const Key('app-icon-rail'),
      width: 56,
      child: Material(
          color: Theme.of(context).colorScheme.surfaceContainerLow,
          child: SafeArea(
              child: Column(children: [
            const Padding(
                padding: EdgeInsets.symmetric(vertical: 18),
                child: PixelBoat(size: 26)),
            Expanded(
                child: SingleChildScrollView(
                    child: Column(children: [
              for (final item in destinations) _button(context, item),
            ]))),
            _button(
                context,
                SidebarDestination(
                    label: 'Settings',
                    icon: Icons.tune_outlined,
                    selected: settingsSelected,
                    onTap: onSettings)),
            const SizedBox(height: 12),
          ]))));
}
