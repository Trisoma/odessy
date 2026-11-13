import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

import '../../../app/session_controller.dart';
import '../../../app/theme.dart';
import '../../monitoring/presentation/metric_presentation.dart';

enum _DeviceSection { overview, settings, history }

/// Inspects a device without changing the session's execution target.
class DevicePage extends StatefulWidget {
  const DevicePage(
      {super.key,
      required this.device,
      required this.controller,
      this.host,
      this.monitoringOverview,
      this.monitoringHistory,
      this.onManagePairing,
      this.onPair});
  final String device;

  /// Verified inventory entry for [device]. Null keeps the demo/placeholder
  /// view, which never claims a live device state.
  final MonitoringHost? host;
  final DesktopSession controller;
  final Widget? monitoringOverview, monitoringHistory;
  final VoidCallback? onManagePairing, onPair;

  @override
  State<DevicePage> createState() => _DevicePageState();
}

class _DevicePageState extends State<DevicePage> {
  _DeviceSection section = _DeviceSection.overview;

  @override
  void didUpdateWidget(covariant DevicePage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.device != widget.device ||
        oldWidget.host?.id != widget.host?.id ||
        oldWidget.controller != widget.controller) {
      section = _DeviceSection.overview;
    }
  }

  MonitoringHost? get host => widget.host;
  bool get _restricted =>
      host != null && (host!.access != 'allowed' || host!.trust == 'revoked');

  /// Wire enum wording, kept literal so nothing unknown reads as healthy.
  String _statusWord(String raw) => switch (raw) {
        'online' => 'Online',
        'stale' => 'Stale',
        'offline' => 'Offline',
        'allowed' => 'Allowed',
        'denied' => 'Denied',
        'unsupported' => 'Unsupported',
        'disabled' => 'Disabled',
        'local' => 'Local',
        'paired' => 'Paired',
        'revoked' => 'Revoked',
        _ => 'Unknown',
      };

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) => LayoutBuilder(
          builder: (context, box) {
            final inset = box.maxWidth < 600
                ? OdessyTokens.spaceRegular
                : OdessyTokens.spaceLarge;
            return SingleChildScrollView(
              key: const Key('device-detail-scroll'),
              padding: EdgeInsets.all(inset),
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 1040),
                  child: widget.device.trim().isEmpty
                      ? _empty(context)
                      : Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            _header(context),
                            const SizedBox(height: OdessyTokens.spaceLarge),
                            if (widget.monitoringHistory != null)
                              Wrap(spacing: 8, runSpacing: 8, children: [
                                for (final entry in [
                                  (_DeviceSection.overview, 'Overview'),
                                  (_DeviceSection.settings, 'Settings'),
                                  (_DeviceSection.history, 'History')
                                ])
                                  Semantics(
                                      selected: section == entry.$1,
                                      child: OutlinedButton(
                                          style: OutlinedButton.styleFrom(
                                              backgroundColor:
                                                  section == entry.$1
                                                      ? Theme.of(context)
                                                          .colorScheme
                                                          .secondaryContainer
                                                      : null,
                                              foregroundColor:
                                                  section == entry.$1
                                                      ? Theme.of(context)
                                                          .colorScheme
                                                          .onSecondaryContainer
                                                      : null),
                                          onPressed: () => setState(
                                              () => section = entry.$1),
                                          child: Text(entry.$2)))
                              ])
                            else
                              SegmentedButton<_DeviceSection>(
                                segments: const [
                                  ButtonSegment(
                                    value: _DeviceSection.overview,
                                    icon: Icon(Icons.dashboard_outlined),
                                    label: Text('Overview'),
                                  ),
                                  ButtonSegment(
                                    value: _DeviceSection.settings,
                                    icon: Icon(Icons.tune_outlined),
                                    label: Text('Settings'),
                                  ),
                                ],
                                selected: {section},
                                onSelectionChanged: (value) =>
                                    setState(() => section = value.single),
                              ),
                            const SizedBox(height: OdessyTokens.spaceLarge),
                            if (section == _DeviceSection.overview)
                              widget.monitoringOverview ?? _overview(context)
                            else if (section == _DeviceSection.history)
                              widget.monitoringHistory!
                            else
                              _settings(context),
                          ],
                        ),
                ),
              ),
            );
          },
        ),
      );

  Widget _header(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('DEVICES',
              style: Theme.of(context).textTheme.labelMedium?.copyWith(
                  color: Theme.of(context).colorScheme.primary,
                  letterSpacing: 1.1)),
          const SizedBox(height: OdessyTokens.spaceRegular),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                padding: const EdgeInsets.all(OdessyTokens.spaceMedium),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.primaryContainer,
                  borderRadius:
                      BorderRadius.circular(OdessyTokens.radiusMedium),
                ),
                child: Icon(
                  host != null
                      ? (host!.trust == 'local'
                          ? Icons.laptop_windows_outlined
                          : Icons.dns_outlined)
                      : widget.device == 'Jetson'
                          ? Icons.memory_outlined
                          : Icons.computer_outlined,
                  size: 28,
                  color: Theme.of(context).colorScheme.onPrimaryContainer,
                ),
              ),
              const SizedBox(width: OdessyTokens.spaceRegular),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Semantics(
                      header: true,
                      child: SelectableText(
                        widget.device,
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                    ),
                    const SizedBox(height: OdessyTokens.spaceSmall),
                    Text(
                      host == null
                          ? 'Device details and available settings.'
                          : connectionMethodLabel(host!.trust),
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: OdessyTokens.spaceRegular),
          Wrap(
            spacing: OdessyTokens.spaceCompact,
            runSpacing: OdessyTokens.spaceCompact,
            children: [
              Chip(
                avatar: Icon(
                  widget.controller.api.isDemo
                      ? Icons.science_outlined
                      : Icons.visibility_outlined,
                  size: 16,
                ),
                label: Text(
                  widget.controller.api.isDemo
                      ? 'Demo device · illustrative state'
                      : 'Read-only device view',
                ),
              ),
              if (host != null)
                Chip(
                  avatar: Icon(
                      host!.connectivity == 'online'
                          ? Icons.wifi
                          : Icons.wifi_off_outlined,
                      size: 16),
                  label: Text('Connection: ${host!.connectivity}'),
                ),
              if (host != null)
                Chip(
                  key: const ValueKey('device-telemetry-chip'),
                  avatar: const Icon(Icons.monitor_heart_outlined, size: 16),
                  label: Text('Telemetry: ${host!.access}'),
                ),
            ],
          ),
          const SizedBox(height: OdessyTokens.spaceCompact),
          Text(
            'Inspecting a device does not change the conversation coordinator or execution target.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      );

  Widget _overview(BuildContext context) {
    final c = widget.controller;
    final loading = c.busy && c.session == null;
    final serviceState = loading
        ? 'Connecting'
        : c.connected
            ? 'Connected'
            : 'Offline';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (loading) ...[
          const LinearProgressIndicator(
            semanticsLabel: 'Connecting to session service',
          ),
          const SizedBox(height: OdessyTokens.spaceRegular),
        ],
        if (c.error.isNotEmpty) ...[
          _sectionCard(
            context,
            'Session needs attention',
            Icons.error_outline,
            [SelectableText(c.error)],
          ),
          const SizedBox(height: OdessyTokens.spaceRegular),
        ],
        LayoutBuilder(
          builder: (context, box) {
            final columns = box.maxWidth >= 780
                ? 3
                : box.maxWidth >= 520
                    ? 2
                    : 1;
            final width =
                (box.maxWidth - (columns - 1) * OdessyTokens.spaceMedium) /
                    columns;
            return Wrap(
              spacing: OdessyTokens.spaceMedium,
              runSpacing: OdessyTokens.spaceMedium,
              children: [
                SizedBox(
                  width: width,
                  child: _statusCard(
                    context,
                    'Session service',
                    serviceState,
                    c.api.isDemo
                        ? 'Demo session only. No device is contacted.'
                        : 'This is the conversation service connection.',
                    loading
                        ? Icons.sync
                        : c.connected
                            ? Icons.check_circle_outline
                            : Icons.cloud_off_outlined,
                  ),
                ),
                SizedBox(
                  width: width,
                  child: _statusCard(
                    context,
                    'Device connection',
                    host == null ? 'Unknown' : _statusWord(host!.connectivity),
                    host == null
                        ? 'No connection report is available for this device.'
                        : 'Reported separately from trust and from telemetry access.',
                    host != null && host!.connectivity == 'online'
                        ? Icons.wifi
                        : Icons.wifi_off_outlined,
                  ),
                ),
                SizedBox(
                  width: width,
                  child: _statusCard(
                    context,
                    'Telemetry access',
                    host == null ? 'Not verified' : _statusWord(host!.access),
                    host == null
                        ? 'Monitoring permission has not been verified here.'
                        : _restricted
                            ? 'No readings are shown while monitoring access is withheld.'
                            : 'Read-only collection only; it grants no execution or workspace.',
                    Icons.shield_outlined,
                  ),
                ),
              ],
            );
          },
        ),
        const SizedBox(height: OdessyTokens.spaceLarge),
        _sectionCard(context, 'Session context', Icons.account_tree_outlined, [
          _detail(
            context,
            'Coordinator',
            c.api.isDemo ? 'Demo session' : c.api.identity,
          ),
          if (host != null) ...[
            _detail(context, 'Device identity', host!.id),
            _detail(context, 'Connection method',
                connectionMethodLabel(host!.trust)),
            _detail(context, 'Monitoring trust', _statusWord(host!.trust)),
          ],
          _detail(context, 'Execution target', 'Managed by backend policy'),
          _detail(context, 'Execution capability', monitoringExecutionNote),
          _detail(context, 'Workspace capability', monitoringWorkspaceNote),
          _detail(
            context,
            'Activity',
            c.session == null
                ? 'No session'
                : c.session!.running
                    ? 'Turn in progress'
                    : 'No active turn',
          ),
          const SizedBox(height: OdessyTokens.spaceMedium),
          Align(
            alignment: Alignment.centerLeft,
            child: OutlinedButton.icon(
              onPressed: c.busy || c.stopping
                  ? null
                  : () async {
                      if (c.session == null) {
                        await c.start();
                      } else {
                        await c.refresh();
                      }
                    },
              icon: const Icon(Icons.refresh, size: 18),
              label: const Text('Refresh session'),
            ),
          ),
        ]),
        const SizedBox(height: OdessyTokens.spaceRegular),
        _sectionCard(context, 'Device metrics', Icons.monitor_heart_outlined, [
          Text(
            widget.monitoringOverview != null
                ? 'Reported readings are shown in Overview.'
                : 'Metrics unavailable',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: OdessyTokens.spaceCompact),
          Text(widget.monitoringOverview != null
              ? 'Select a collector group there for its status, source and sample time. This page never infers a value.'
              : 'No device metrics are available in this view.'),
          const SizedBox(height: OdessyTokens.spaceRegular),
          _detail(
              context,
              'CPU',
              widget.monitoringOverview != null
                  ? 'See collector report'
                  : 'Unavailable'),
          _detail(
              context,
              'Memory',
              widget.monitoringOverview != null
                  ? 'See collector report'
                  : 'Unavailable'),
          _detail(
              context,
              'GPU',
              widget.monitoringOverview != null
                  ? 'See collector report'
                  : 'Unavailable'),
          const SizedBox(height: OdessyTokens.spaceCompact),
          Text(
            'Missing readings do not mean zero usage.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ]),
      ],
    );
  }

  Widget _settings(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _sectionCard(context, 'Device configuration', Icons.tune_outlined, [
            const Text('Device configuration is unavailable.'),
            const SizedBox(height: OdessyTokens.spaceCompact),
            Text(
              'These settings belong to ${widget.device}. Global preferences remain in the sidebar Settings page.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ]),
          const SizedBox(height: OdessyTokens.spaceRegular),
          _sectionCard(context, 'Pairing & access', Icons.shield_outlined, [
            _detail(context, 'Access status',
                host == null ? 'Not verified' : _statusWord(host!.access)),
            const Text(
                'Pairing and permissions are managed on this computer. Verify the peer identity before changing access; selecting this device does not identify or authorize a peer.'),
            const SizedBox(height: OdessyTokens.spaceRegular),
            Tooltip(
              message: (widget.onManagePairing ?? widget.onPair) == null
                  ? 'Local pairing tools are unavailable in this context.'
                  : 'Open local pairing tools without changing access.',
              child: Align(
                alignment: Alignment.centerLeft,
                child: OutlinedButton(
                    onPressed: widget.onManagePairing ?? widget.onPair,
                    child: const Text('Manage pairing')),
              ),
            ),
          ]),
          const SizedBox(height: OdessyTokens.spaceRegular),
          _sectionCard(context, 'Voice & audio', Icons.mic_none_outlined, [
            _detail(context, 'Desktop audio', 'Unavailable'),
            const Text(
              'Desktop microphone controls are not available here. Use your existing voice setup.',
            ),
            const SizedBox(height: OdessyTokens.spaceRegular),
            const Tooltip(
              message: 'Desktop audio device controls are unavailable.',
              child: Align(
                alignment: Alignment.centerLeft,
                child: OutlinedButton(
                  onPressed: null,
                  child: Text('Configure audio'),
                ),
              ),
            ),
          ]),
        ],
      );

  Widget _statusCard(
    BuildContext context,
    String title,
    String value,
    String description,
    IconData icon,
  ) =>
      ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 164),
        child: _sectionCard(context, title, icon, [
          Text(value, style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: OdessyTokens.spaceCompact),
          Text(description, style: Theme.of(context).textTheme.bodySmall),
        ]),
      );

  Widget _sectionCard(
    BuildContext context,
    String title,
    IconData icon,
    List<Widget> children,
  ) =>
      Card(
        child: Padding(
          padding: const EdgeInsets.all(OdessyTokens.spaceLarge),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(
                    icon,
                    size: 18,
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                  const SizedBox(width: OdessyTokens.spaceCompact),
                  Expanded(
                    child: Text(
                      title,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: OdessyTokens.spaceRegular),
              ...children,
            ],
          ),
        ),
      );

  Widget _detail(BuildContext context, String label, String value) => Padding(
        padding:
            const EdgeInsets.symmetric(vertical: OdessyTokens.spaceCompact),
        child: LayoutBuilder(
          builder: (context, box) {
            final labelWidget = Text(
              label,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant),
            );
            final valueWidget = SelectableText(value);
            if (box.maxWidth < 460) {
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  labelWidget,
                  const SizedBox(height: OdessyTokens.spaceSmall),
                  valueWidget,
                ],
              );
            }
            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(width: 160, child: labelWidget),
                const SizedBox(width: OdessyTokens.spaceRegular),
                Expanded(child: valueWidget),
              ],
            );
          },
        ),
      );

  Widget _empty(BuildContext context) => _sectionCard(
        context,
        'No device selected',
        Icons.devices_other_outlined,
        [
          const Text('Choose a device from the sidebar to inspect its details.')
        ],
      );
}
