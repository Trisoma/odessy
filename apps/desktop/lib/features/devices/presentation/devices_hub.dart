import 'package:flutter/material.dart';
import '../../../app/session_controller.dart';
import '../../monitoring/controllers/monitor_controller.dart';
import '../../monitoring/presentation/monitor_page.dart';
import 'devices.dart';

/// Groups existing device inspection, monitoring and native connection tools.
class DevicesHub extends StatefulWidget {
  const DevicesHub(
      {super.key,
      required this.session,
      required this.monitoring,
      required this.onConnect,
      this.initialPerformance = false});
  final DesktopSession session;
  final MonitorController monitoring;
  final VoidCallback? onConnect;
  final bool initialPerformance;
  @override
  State<DevicesHub> createState() => _DevicesHubState();
}

class _DevicesHubState extends State<DevicesHub> {
  late bool performance;
  String device = 'This computer';
  @override
  void initState() {
    super.initState();
    performance = widget.initialPerformance;
    if (performance) widget.monitoring.bind(widget.session.api);
    if (!widget.session.api.isDemo) widget.monitoring.bind(widget.session.api);
  }

  @override
  Widget build(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
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
                      onSelected: (_) => setState(() => performance = false)),
                  ChoiceChip(
                      label: const Text('Performance'),
                      selected: performance,
                      onSelected: (_) {
                        widget.monitoring.bind(widget.session.api);
                        setState(() => performance = true);
                      }),
                  OutlinedButton.icon(
                      onPressed: widget.onConnect,
                      icon: const Icon(Icons.add, size: 18),
                      label: const Text('Connect a device')),
                  if (widget.session.api.isDemo && !performance)
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
                ])),
        Expanded(
            child: performance
                ? MonitorPage(controller: widget.monitoring)
                : widget.session.api.isDemo
                    ? DevicePage(
                        device: device,
                        controller: widget.session,
                        onPair: widget.onConnect)
                    : ListenableBuilder(
                        listenable: widget.monitoring,
                        builder: (context, _) => ListView(
                                padding: const EdgeInsets.all(24),
                                children: [
                                  Text('Device inventory',
                                      style: Theme.of(context)
                                          .textTheme
                                          .titleLarge),
                                  const Text(
                                      'Inspection only. Online status never grants tool access or changes the coordinator.'),
                                  if (widget.monitoring.busy)
                                    const LinearProgressIndicator(),
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
                                          icon: const Icon(
                                              Icons.monitor_heart_outlined),
                                          onPressed: () {
                                            widget.monitoring.select(host.id);
                                            setState(() => performance = true);
                                          }),
                                    )),
                                  const SizedBox(height: 16),
                                  TextButton(
                                      onPressed: widget.monitoring.busy
                                          ? null
                                          : widget.monitoring.refresh,
                                      child: const Text('Refresh inventory')),
                                ]))),
      ]);
}
