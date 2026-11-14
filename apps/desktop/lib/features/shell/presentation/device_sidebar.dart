import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/theme.dart';
import '../../../widgets/pixel_boat.dart';

/// A presentation destination. Routing and command admission belong to the app.
class SidebarDestination {
  const SidebarDestination(
      {required this.label,
      required this.icon,
      required this.onTap,
      this.selected = false});
  final String label;
  final IconData icon;
  final VoidCallback? onTap;
  final bool selected;
}

/// Data and callbacks only; this widget never creates clients or selects targets.
class DeviceSidebar extends StatelessWidget {
  const DeviceSidebar(
      {super.key,
      required this.hosts,
      required this.onInspect,
      required this.onConversation,
      required this.onSettings,
      this.onRefresh,
      this.onSearch,
      this.onNewSession,
      this.onPair,
      this.selectedHostId,
      this.conversationSelected = false,
      this.settingsSelected = false,
      this.loading = false,
      this.retained = false,
      this.error,
      this.demo = false,
      this.connectionLabel = 'Service not connected',
      this.conversationLabel = 'Conversation',
      this.navigationFocus,
      this.primaryDestinations = const [],
      this.projectNavigation,
      this.sessionSearch,
      this.projectOnly = false,
      this.destinations = const []});
  final bool projectOnly;
  final Widget? projectNavigation, sessionSearch;
  final List<MonitoringHost> hosts;
  final ValueChanged<String> onInspect;
  final VoidCallback onConversation, onSettings;
  final VoidCallback? onRefresh, onPair, onNewSession, onSearch;
  final String? selectedHostId, error;
  final bool conversationSelected, settingsSelected, loading, retained, demo;
  final String connectionLabel;
  final String conversationLabel;
  final Map<String, FocusNode>? navigationFocus;
  final List<SidebarDestination> primaryDestinations, destinations;
  bool get _lastKnown => hosts.isNotEmpty && (retained || error != null);

  @override
  Widget build(BuildContext context) => SafeArea(
          child: Column(children: [
        Padding(
            padding: const EdgeInsets.fromLTRB(24, 20, 16, 20),
            child: Row(children: [
              if (!projectOnly) const PixelBoat(size: 30),
              if (!projectOnly) const SizedBox(width: 9),
              Expanded(
                  child: Text('Odessy',
                      style: const TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.w600,
                          letterSpacing: -.7))),
              if (onSearch != null)
                IconButton(
                    tooltip: 'Search chats',
                    onPressed: onSearch,
                    icon: const Icon(Icons.search, size: 20)),
            ])),
        Expanded(
            child: ListView(
                key: const Key('device-sidebar-scroll'),
                padding: const EdgeInsets.symmetric(horizontal: 12),
                children: [
              if (sessionSearch != null)
                Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: sessionSearch!),
              Padding(
                  padding: EdgeInsets.only(
                      bottom: projectOnly ? 4 : 8, top: projectOnly ? 0 : 4),
                  child: Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton.icon(
                          onPressed: onNewSession,
                          icon: const Icon(Icons.add, size: 18),
                          label: Text(projectOnly ? 'New chat' : 'New session',
                              style: const TextStyle(fontSize: 14))))),
              if (!projectOnly) ...[
                _navigation(
                    context,
                    conversationLabel,
                    Icons.chat_bubble_outline,
                    conversationSelected,
                    onConversation),
                for (final destination in primaryDestinations)
                  _navigation(context, destination.label, destination.icon,
                      destination.selected, destination.onTap),
              ],
              if (projectNavigation != null) projectNavigation!,
              if (!projectOnly) ...[
                const SizedBox(height: 20),
                Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: Row(children: [
                      Expanded(
                          child: Semantics(
                              header: true,
                              child: Text('Computers',
                                  style: Theme.of(context)
                                      .textTheme
                                      .labelMedium
                                      ?.copyWith(
                                          color: Theme.of(context)
                                              .colorScheme
                                              .onSurfaceVariant)))),
                      IconButton(
                          tooltip: 'Refresh devices',
                          icon: const Icon(Icons.refresh, size: 18),
                          onPressed:
                              loading && hosts.isEmpty ? null : onRefresh),
                    ])),
                if (demo)
                  const Padding(
                      padding: EdgeInsets.fromLTRB(12, 0, 12, 12),
                      child: Text('Demo devices · fixtures only')),
                if (loading && hosts.isEmpty)
                  const LinearProgressIndicator(
                      semanticsLabel: 'Loading devices'),
                if (error != null)
                  Padding(
                      padding: const EdgeInsets.all(12), child: Text(error!)),
                if (_lastKnown)
                  const Padding(
                      padding: EdgeInsets.all(12),
                      child: Text('Last known device list')),
                if (hosts.isEmpty && !loading)
                  Padding(
                      padding: const EdgeInsets.all(12),
                      child: Text(error == null
                          ? 'No devices reported'
                          : 'Device list unavailable')),
                for (final host in hosts)
                  Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: ListTile(
                        key: ValueKey('inspect-${host.id}'),
                        selected: selectedHostId == host.id,
                        selectedTileColor: Theme.of(context)
                            .colorScheme
                            .primary
                            .withValues(alpha: .12),
                        shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(
                                OdessyTokens.radiusSmall)),
                        dense: true,
                        contentPadding:
                            const EdgeInsets.symmetric(horizontal: 12),
                        leading: Icon(
                            host.trust == 'local'
                                ? Icons.computer_outlined
                                : host.trust == 'revoked'
                                    ? Icons.link_off
                                    : Icons.devices_other_outlined,
                            size: 20),
                        title: Text(host.name,
                            maxLines: 3,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodyMedium),
                        subtitle: Text(
                            '${_label(host.connectivity)} · ${_label(host.trust)}${_lastKnown ? ' · last report' : ''}',
                            style: Theme.of(context)
                                .textTheme
                                .bodySmall
                                ?.copyWith(
                                    color: Theme.of(context)
                                        .colorScheme
                                        .onSurfaceVariant)),
                        onTap: () => onInspect(host.id),
                      )),
                Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton.icon(
                        onPressed: onPair,
                        icon: const Icon(Icons.add, size: 18),
                        label: const Text('Pair a device'))),
              ],
            ])),
        if (!projectOnly) const Divider(height: 1),
        Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (!projectOnly)
                    _navigation(context, 'Settings', Icons.tune_outlined,
                        settingsSelected, onSettings),
                  if (!projectOnly) const SizedBox(height: 8),
                  if (!projectOnly)
                    Tooltip(
                        message: connectionLabel,
                        child: Text(connectionLabel,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodySmall)),
                ])),
      ]));

  Widget _navigation(BuildContext context, String title, IconData icon,
      bool selected, VoidCallback? onTap) {
    final focus = navigationFocus?.putIfAbsent(
        title, () => FocusNode(debugLabel: 'Navigation: $title'));
    return ListTile(
      key: ValueKey('nav-$title'),
      focusNode: focus,
      selected: selected,
      dense: true,
      minVerticalPadding: 10,
      selectedTileColor: Theme.of(context).colorScheme.secondaryContainer,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      leading: Icon(icon, size: 19),
      title: Text(title,
          style: TextStyle(
              fontSize: 13,
              fontWeight: selected ? FontWeight.w700 : FontWeight.w500)),
      onTap: onTap == null
          ? null
          : () {
              focus?.requestFocus();
              FocusManager.instance.applyFocusChangesIfNeeded();
              onTap();
            },
    );
  }

  String _label(String value) => switch (value) {
        'online' => 'Online',
        'offline' => 'Offline',
        'stale' => 'Stale',
        'local' => 'Local',
        'paired' => 'Paired',
        'revoked' => 'Revoked',
        _ => 'Unknown',
      };
}
