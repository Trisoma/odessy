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
                : DevicePage(
                    device: device,
                    controller: widget.session,
                    onPair: widget.onConnect)),
      ]);
}
