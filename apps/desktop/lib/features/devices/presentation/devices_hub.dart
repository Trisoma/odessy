import 'dart:async';

import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';
import '../../monitoring/controllers/monitor_controller.dart';
import '../../monitoring/presentation/metric_presentation.dart';
import '../../monitoring/presentation/monitor_page.dart';
import '../controllers/device_monitoring_controller.dart';
import 'devices.dart';
import 'monitoring_details.dart';
import 'monitoring_history.dart';

/// Groups existing device inspection, monitoring and native connection tools.
///
/// In live mode it drives exactly one monitoring owner: when [devices] is
/// supplied it is the only poller, so the legacy [monitoring] controller is not
/// started next to it. Demo mode keeps the fixture path.
class DevicesHub extends StatefulWidget {
  const DevicesHub(
      {super.key,
      required this.session,
      required this.monitoring,
      required this.onConnect,
      this.devices,
      this.onManagePairing,
      this.initialPerformance = false});
  final DesktopSession session;
  final MonitorController monitoring;
  final VoidCallback? onConnect;

  /// Verified inventory/monitoring owner. Null keeps the demo and legacy paths.
  final DeviceMonitoringController? devices;
  final VoidCallback? onManagePairing;
  final bool initialPerformance;
  @override
  State<DevicesHub> createState() => _DevicesHubState();
}

class _DevicesHubState extends State<DevicesHub> {
  late bool performance;
  bool details = false;
  String device = 'This computer';

  @override
  void initState() {
    super.initState();
    performance = widget.initialPerformance;
    final devices = widget.devices;
    if (devices != null) {
      // One owner for inventory, metrics and events; bind is idempotent.
      devices.bind(widget.session.api);
    } else if (!widget.session.api.isDemo) {
      widget.monitoring.bind(widget.session.api);
    }
  }

  void _select(MonitoringHost host) {
    widget.devices?.select(host.id);
    setState(() => details = true);
  }

  @override
  Widget build(BuildContext context) {
    final devices = widget.devices;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Padding(
          padding: const EdgeInsets.all(24),
          child: Wrap(
              spacing: 12,
              runSpacing: 12,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                ChoiceChip(
                    label: const Text('Overview'),
                    selected: !performance,
                    onSelected: (_) => setState(() {
                          performance = false;
                          details = false;
                        })),
                ChoiceChip(
                    label: const Text('Performance'),
                    selected: performance,
                    onSelected: (_) {
                      if (devices == null) {
                        widget.monitoring.bind(widget.session.api);
                      }
                      setState(() => performance = true);
                    }),
                OutlinedButton.icon(
                    onPressed: widget.onConnect,
                    icon: const Icon(Icons.add, size: 18),
                    label: const Text('Connect a device')),
                if (details && !performance)
                  TextButton.icon(
                      onPressed: () => setState(() => details = false),
                      icon: const Icon(Icons.arrow_back, size: 18),
                      label: const Text('All computers')),
                if (widget.session.api.isDemo &&
                    !performance &&
                    devices == null)
                  DropdownButton<String>(
                      value: device,
                      items: [
                        for (final value in [
                          'This computer',
                          'Jetson',
                          'Laptop host'
                        ])
                          DropdownMenuItem(value: value, child: Text(value))
                      ],
                      onChanged: (value) => setState(() => device = value!)),
                if (devices != null && !performance)
                  TextButton.icon(
                      onPressed: devices.loadingInventory
                          ? null
                          : () => unawaited(devices.refresh()),
                      icon: const Icon(Icons.refresh, size: 18),
                      label: const Text('Refresh inventory')),
              ])),
      Expanded(
          child: performance
              ? (devices != null
                  ? _livePerformance(context, devices)
                  : MonitorPage(controller: widget.monitoring))
              : devices != null
                  ? (details
                      ? _liveDetails(context, devices)
                      : _inventory(context, devices))
                  : widget.session.api.isDemo
                      ? DevicePage(
                          device: device,
                          controller: widget.session,
                          onPair: widget.onConnect)
                      : _legacyInventory(context)),
    ]);
  }

  Widget _inventory(BuildContext context, DeviceMonitoringController c) =>
      ListenableBuilder(
          listenable: c,
          builder: (context, _) {
            final hosts = c.hosts;
            return ListView(
                key: const Key('devices-overview-scroll'),
                padding: const EdgeInsets.all(24),
                children: [
                  Text('Computers and monitoring',
                      style: Theme.of(context).textTheme.headlineSmall),
                  const SizedBox(height: 6),
                  const Text(
                      'Identity, connection, telemetry and execution permissions are separate states. Inspecting a computer never changes the conversation coordinator.'),
                  const SizedBox(height: 16),
                  if (c.loadingInventory && hosts.isEmpty)
                    const LinearProgressIndicator(
                        semanticsLabel: 'Loading computer inventory'),
                  if (c.inventoryError != null)
                    Card(
                        child: ListTile(
                            leading: const Icon(Icons.error_outline),
                            title: const Text('Inventory needs attention'),
                            subtitle: Text(c.inventoryError!),
                            trailing: TextButton(
                                onPressed: () => unawaited(c.refresh()),
                                child: const Text('Retry')))),
                  if (hosts.isEmpty &&
                      !c.loadingInventory &&
                      c.inventoryError == null)
                    const Card(
                        child: ListTile(
                            leading: Icon(Icons.devices_other_outlined),
                            title: Text('No verified computers yet'),
                            subtitle: Text(
                                'Connect a computer through pairing, or check monitoring access and grants. No device state is assumed.'))),
                  LayoutBuilder(builder: (context, box) {
                    final columns =
                        box.maxWidth >= 900 ? 3 : (box.maxWidth >= 620 ? 2 : 1);
                    final width = (box.maxWidth - (columns - 1) * 12) / columns;
                    return Wrap(spacing: 12, runSpacing: 12, children: [
                      for (final host in hosts)
                        SizedBox(
                            width: width, child: _hostCard(context, c, host)),
                    ]);
                  }),
                  const SizedBox(height: 16),
                  const Text(
                      'Monitoring is read-only. It grants no execution or workspace access.',
                      style: TextStyle(fontSize: 12)),
                ]);
          });

  Widget _hostCard(BuildContext context, DeviceMonitoringController c,
          MonitoringHost h) =>
      Card(
        key: ValueKey('host-card-${h.id}'),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () => _select(h),
          child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Icon(
                          h.trust == 'local'
                              ? Icons.laptop_windows_outlined
                              : Icons.dns_outlined,
                          size: 22),
                      const SizedBox(width: 8),
                      Expanded(
                          child: Text(h.name,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.titleMedium)),
                    ]),
                    const SizedBox(height: 8),
                    Text(connectionMethodLabel(h.trust),
                        style: Theme.of(context).textTheme.bodySmall),
                    const SizedBox(height: 10),
                    Wrap(spacing: 6, runSpacing: 6, children: [
                      _tag(context, 'Connection: ${h.connectivity}'),
                      _tag(context, 'Telemetry: ${h.access}'),
                      if (h.trust == 'revoked')
                        _tag(context, 'Pairing revoked', error: true),
                    ]),
                    const SizedBox(height: 10),
                    Align(
                        alignment: Alignment.centerLeft,
                        child: TextButton(
                            onPressed: () => _select(h),
                            child: const Text('View details'))),
                  ])),
        ),
      );

  Widget _tag(BuildContext context, String text,
          {bool error = false}) =>
      Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
          decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(6),
              border: Border.all(
                  color: error
                      ? Theme.of(context).colorScheme.error
                      : Theme.of(context).colorScheme.outlineVariant)),
          child: Text(text, style: const TextStyle(fontSize: 11)));

  Widget _liveDetails(BuildContext context, DeviceMonitoringController c) =>
      DevicePage(
          key: ValueKey('device-details-${c.selectedId}'),
          device: c.inspectedHost?.name ?? c.selectedHost?.name ?? '',
          host: c.inspectedHost,
          controller: widget.session,
          onManagePairing: widget.onManagePairing,
          monitoringOverview: MonitoringDetails(
              showHeader: true,
              host: c.inspectedHost,
              report: c.metrics,
              loading: c.loadingMetrics || c.loadingInventory,
              retained: c.metricsRetained,
              error: c.metricsError ?? c.inventoryError,
              demo: c.demo,
              onRefresh: () => unawaited(c.refreshSelected())),
          monitoringHistory:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            DropdownButton<HistoryWindow>(
                key: const Key('monitoring-history-window'),
                isExpanded: true,
                value: c.window,
                items: [
                  for (final item in HistoryWindow.values)
                    DropdownMenuItem(value: item, child: Text(item.label))
                ],
                onChanged: (value) {
                  if (value != null) c.setWindow(value);
                }),
            MonitoringHistory(
                host: c.inspectedHost,
                report: c.history,
                loading: c.loadingHistory,
                retained: c.historyRetained,
                error: c.historyError,
                degradation: c.degradation,
                demo: c.demo,
                onRefresh: () => unawaited(c.refreshHistory())),
          ]));

  Widget _livePerformance(BuildContext context, DeviceMonitoringController c) =>
      ListenableBuilder(
          listenable: c,
          builder: (context, _) =>
              ListView(padding: const EdgeInsets.all(24), children: [
                Row(children: [
                  Expanded(
                      child: Text('Monitoring performance',
                          style: Theme.of(context).textTheme.titleLarge)),
                  IconButton(
                      tooltip: 'Refresh monitoring',
                      onPressed: c.loadingInventory
                          ? null
                          : () => unawaited(c.refresh()),
                      icon: const Icon(Icons.refresh)),
                ]),
                const Text(
                    'Only fields the collector reported are shown. Unknown, denied and stale readings are labelled, never replaced by zero.'),
                const SizedBox(height: 12),
                if (c.hosts.isEmpty)
                  const Card(
                      child: ListTile(
                          leading: Icon(Icons.devices_other_outlined),
                          title: Text('No verified computers yet'),
                          subtitle: Text(
                              'Performance appears after a computer reports monitoring data.'))),
                if (c.hosts.isNotEmpty)
                  Wrap(spacing: 8, runSpacing: 8, children: [
                    for (final h in c.hosts)
                      ChoiceChip(
                          label: Text('${h.name} · ${h.connectivity}'),
                          selected: h.id == c.selectedId,
                          onSelected: (_) => c.select(h.id))
                  ]),
                const SizedBox(height: 12),
                if (c.selectedId != null)
                  MonitoringDetails(
                      host: c.inspectedHost,
                      report: c.metrics,
                      loading: c.loadingMetrics || c.loadingInventory,
                      retained: c.metricsRetained,
                      error: c.metricsError ?? c.inventoryError,
                      demo: c.demo,
                      showHeader: false,
                      onRefresh: () => unawaited(c.refreshSelected())),
                if (c.selectedId != null) ...[
                  const SizedBox(height: 8),
                  Text('CPU history · ${c.window.label}',
                      style: Theme.of(context).textTheme.titleMedium),
                  MonitoringHistory(
                      host: c.inspectedHost,
                      report: c.history,
                      loading: c.loadingHistory,
                      retained: c.historyRetained,
                      error: c.historyError,
                      degradation: c.degradation,
                      demo: c.demo,
                      onRefresh: () => unawaited(c.refreshHistory())),
                ],
              ]));

  Widget _legacyInventory(BuildContext context) => ListenableBuilder(
      listenable: widget.monitoring,
      builder: (context, _) =>
          ListView(padding: const EdgeInsets.all(24), children: [
            Text('Device inventory',
                style: Theme.of(context).textTheme.titleLarge),
            const Text(
                'Inspection only. Online status never grants tool access or changes the coordinator.'),
            if (widget.monitoring.busy) const LinearProgressIndicator(),
            if (widget.monitoring.message.isNotEmpty)
              Text(widget.monitoring.message),
            if (widget.monitoring.hosts.isEmpty)
              const Text(
                  'No verified inventory available. Connect a device through pairing or check monitoring access.'),
            for (final host in widget.monitoring.hosts)
              Card(
                  child: ListTile(
                key: ValueKey('inventory-${host.id}'),
                title: Text(host.name),
                subtitle: SelectableText(
                    'Identity: ${host.id}\nTrust: ${host.trust} · Connection: ${host.connectivity} · Telemetry: ${host.access}'),
                trailing: IconButton(
                    tooltip: 'Inspect performance',
                    icon: const Icon(Icons.monitor_heart_outlined),
                    onPressed: () {
                      widget.monitoring.select(host.id);
                      setState(() => performance = true);
                    }),
              )),
            const SizedBox(height: 16),
            TextButton(
                onPressed:
                    widget.monitoring.busy ? null : widget.monitoring.refresh,
                child: const Text('Refresh inventory')),
          ]));
}
