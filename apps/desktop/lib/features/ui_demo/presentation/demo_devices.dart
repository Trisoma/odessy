import 'package:flutter/material.dart';

import 'demo_style.dart';

class DemoWorkspaces extends StatelessWidget {
  const DemoWorkspaces({super.key, required this.onOpen});
  final VoidCallback onOpen;
  @override
  Widget build(BuildContext context) => Center(
          child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1080),
        child: ListView(padding: const EdgeInsets.all(36), children: [
          const Align(
              alignment: Alignment.centerLeft,
              child: DemoTag('Workspace concept · fixture data',
                  icon: Icons.science_outlined)),
          const SizedBox(height: 18),
          const Text('A place for your work',
              style: TextStyle(
                  fontSize: 30,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -.6)),
          const SizedBox(height: 10),
          const Text(
              'Keep conversations and their context together. Know which computer owns the files.',
              style: TextStyle(
                  color: DemoColors.muted, fontSize: 14, height: 1.8)),
          const SizedBox(height: 28),
          DemoCard(
              title: 'odessy',
              icon: Icons.folder_open,
              description: 'Sample project · This computer',
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Wrap(spacing: 8, runSpacing: 8, children: [
                      DemoTag('Local fixture',
                          color: DemoColors.mint, icon: Icons.laptop_windows),
                      DemoTag('3 conversations',
                          icon: Icons.chat_bubble_outline),
                      DemoTag('No disk access', icon: Icons.lock_outline)
                    ]),
                    const SizedBox(height: 22),
                    const DemoValueRow(
                        'Recent conversation', 'Review collector reconnect'),
                    const DemoValueRow(
                        'Context', 'Collector, regression example, README'),
                    const DemoValueRow(
                        'Workspace path', r'demo:\odessy · illustrative'),
                    const SizedBox(height: 14),
                    FilledButton.icon(
                        onPressed: onOpen,
                        icon: const Icon(Icons.arrow_forward, size: 18),
                        label: const Text('Open sample conversation')),
                  ])),
          const DemoCard(
              title: 'Computer boundaries',
              icon: Icons.devices_outlined,
              description:
                  'Browsing a computer does not switch the conversation execution target.',
              child: Column(children: [
                DemoValueRow('This computer', 'Owns the sample workspace'),
                DemoValueRow(
                    'Jetson / SSH', 'Monitoring only · no workspace browsing',
                    color: DemoColors.blue),
                DemoValueRow('Remote workspace access', 'Unavailable over SSH',
                    color: DemoColors.amber),
              ])),
          const Text(
              'This project browser is a UI proposal. It does not scan folders, create a workspace registry or modify host configuration.',
              style: TextStyle(
                  fontSize: 12, color: DemoColors.muted, height: 1.8)),
        ]),
      ));
}

class DemoDevices extends StatefulWidget {
  const DemoDevices({super.key});
  @override
  State<DemoDevices> createState() => _DemoDevicesState();
}

class _DemoDevicesState extends State<DemoDevices> {
  bool performance = false;
  String host = 'Jetson';
  String condition = 'Fresh sample';
  static const conditions = [
    'Fresh sample',
    'Disconnected sample',
    'Permission denied sample'
  ];

  Widget computer(String name, String method, IconData icon) => Expanded(
          child: Padding(
        padding: const EdgeInsets.only(right: 12, bottom: 12),
        child: Material(
          color: host == name ? DemoColors.raised : DemoColors.surface,
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
              side: BorderSide(
                  color: host == name ? DemoColors.mint : DemoColors.line)),
          child: InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: () => setState(() => host = name),
            child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(icon,
                          color: name == 'Jetson'
                              ? DemoColors.blue
                              : DemoColors.mint,
                          size: 27),
                      const SizedBox(height: 15),
                      Text(name,
                          style: const TextStyle(
                              fontSize: 17, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 7),
                      Text(method,
                          style: const TextStyle(
                              fontSize: 12, color: DemoColors.muted)),
                      const SizedBox(height: 14),
                      DemoTag(
                          name == 'Jetson'
                              ? 'SSH · monitoring only'
                              : 'Paired · separate permissions',
                          color: name == 'Jetson'
                              ? DemoColors.blue
                              : DemoColors.mint),
                    ])),
          ),
        ),
      ));

  Widget metric(String label, String value, String detail, Color color) =>
      Expanded(
          child: Container(
        margin: const EdgeInsets.only(right: 10, bottom: 10),
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
            color: DemoColors.surface,
            border: Border.all(color: DemoColors.line),
            borderRadius: BorderRadius.circular(12)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label,
              style: const TextStyle(fontSize: 12, color: DemoColors.muted)),
          const SizedBox(height: 14),
          Text(value,
              style: TextStyle(
                  fontSize: 27, fontWeight: FontWeight.w700, color: color)),
          const SizedBox(height: 10),
          Text(detail,
              style: const TextStyle(fontSize: 11, color: DemoColors.muted)),
        ]),
      ));

  @override
  Widget build(BuildContext context) => Center(
          child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1080),
        child: ListView(padding: const EdgeInsets.all(36), children: [
          const Align(
              alignment: Alignment.centerLeft,
              child: DemoTag('Computer inventory · fixture data',
                  icon: Icons.science_outlined)),
          const SizedBox(height: 18),
          const Text('Connected, with clear boundaries',
              style: TextStyle(
                  fontSize: 29,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -.6)),
          const SizedBox(height: 10),
          const Text(
              'Identity, connection, telemetry and action permissions are separate states.',
              style: TextStyle(
                  color: DemoColors.muted, fontSize: 14, height: 1.8)),
          const SizedBox(height: 24),
          Wrap(
              spacing: 10,
              runSpacing: 10,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                ChoiceChip(
                    label: const Text('Overview'),
                    selected: !performance,
                    onSelected: (_) => setState(() => performance = false)),
                ChoiceChip(
                    label: const Text('Performance'),
                    selected: performance,
                    onSelected: (_) => setState(() => performance = true)),
                const SizedBox(width: 8),
                DropdownButton<String>(
                    value: condition,
                    underline: const SizedBox.shrink(),
                    items: conditions
                        .map((s) => DropdownMenuItem(
                            value: s,
                            child:
                                Text(s, style: const TextStyle(fontSize: 12))))
                        .toList(),
                    onChanged: (s) {
                      if (s != null) setState(() => condition = s);
                    }),
              ]),
          const SizedBox(height: 24),
          if (!performance) ...[
            LayoutBuilder(
                builder: (context, size) => size.maxWidth < 600
                    ? Column(children: [
                        SizedBox(
                            child: Row(children: [
                          computer(
                              'This computer',
                              'Local coordinator · sample',
                              Icons.laptop_windows)
                        ])),
                        SizedBox(
                            child: Row(children: [
                          computer('Jetson', 'Existing SSH alias · sample',
                              Icons.memory)
                        ]))
                      ])
                    : Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                            computer(
                                'This computer',
                                'Local coordinator · sample',
                                Icons.laptop_windows),
                            computer('Jetson', 'Existing SSH alias · sample',
                                Icons.memory)
                          ])),
            DemoCard(
                title: '$host · connection details',
                icon: Icons.cable_outlined,
                description:
                    'Selecting this card only changes the inspected fixture.',
                child: Column(children: [
                  DemoValueRow(
                      'Connection method',
                      host == 'Jetson'
                          ? 'SSH / existing alias'
                          : 'Local / paired provider'),
                  const DemoValueRow('Identity',
                      'Illustrative trusted identity · not verified live'),
                  DemoValueRow(
                      'Connection',
                      condition == 'Disconnected sample'
                          ? 'Offline · sample'
                          : 'Connected · sample',
                      color: condition == 'Disconnected sample'
                          ? DemoColors.amber
                          : DemoColors.mint),
                  DemoValueRow(
                      'Telemetry',
                      condition == 'Permission denied sample'
                          ? 'Hidden · permission denied'
                          : condition == 'Disconnected sample'
                              ? 'Stale · last sample retained'
                              : 'Read-only · fresh sample',
                      color: DemoColors.blue),
                  DemoValueRow(
                      'Execution / workspace',
                      host == 'Jetson'
                          ? 'Unavailable for SSH'
                          : 'No grants loaded in demo',
                      color: DemoColors.amber),
                ])),
          ] else ...[
            Wrap(spacing: 8, runSpacing: 8, children: [
              DemoTag('$host · illustrative metrics',
                  icon: Icons.monitor_heart_outlined, color: DemoColors.blue),
              DemoTag(
                  condition == 'Fresh sample'
                      ? 'Source: fixture · age: fixed 2s'
                      : condition,
                  color: condition == 'Fresh sample'
                      ? DemoColors.mint
                      : DemoColors.amber)
            ]),
            const SizedBox(height: 20),
            if (condition == 'Permission denied sample')
              const DemoCard(
                  title: 'Telemetry is unavailable',
                  icon: Icons.lock_outline,
                  description: 'Permission denied · sample state',
                  child: Text(
                      'Previously shown values are hidden. An authenticated SSH connection does not grant telemetry access.',
                      style: TextStyle(
                          fontSize: 14, height: 1.8, color: DemoColors.muted)))
            else ...[
              LayoutBuilder(
                  builder: (context, size) => size.maxWidth < 650
                      ? Column(children: [
                          for (final item in [
                            ('CPU', '23%', 'Sample utilization'),
                            ('Memory', '2.4 GiB', 'Sample resident memory'),
                            ('Temperature', '45°C', 'Sample sensor value')
                          ])
                            SizedBox(
                                child: Row(children: [
                              metric(item.$1, item.$2, item.$3, DemoColors.mint)
                            ]))
                        ])
                      : Row(children: [
                          metric('CPU', '23%', 'Sample utilization',
                              DemoColors.mint),
                          metric('Memory', '2.4 GiB', 'Sample resident memory',
                              DemoColors.blue),
                          metric('Temperature', '45°C', 'Sample sensor value',
                              DemoColors.amber)
                        ])),
              DemoCard(
                  title: 'CPU history · illustrative',
                  icon: Icons.show_chart,
                  description:
                      'Fixed samples, not observations from your computer or Jetson.',
                  child: SizedBox(
                      height: 135,
                      child: Row(
                          crossAxisAlignment: CrossAxisAlignment.end,
                          children: [
                            for (final height in [
                              18.0,
                              26.0,
                              24.0,
                              42.0,
                              35.0,
                              55.0,
                              72.0,
                              46.0,
                              38.0,
                              62.0,
                              50.0,
                              44.0,
                              32.0,
                              45.0,
                              34.0,
                              40.0,
                              30.0,
                              36.0
                            ])
                              Expanded(
                                  child: Container(
                                      height: height + 20,
                                      margin: const EdgeInsets.symmetric(
                                          horizontal: 3),
                                      decoration: BoxDecoration(
                                          color: DemoColors.mint
                                              .withValues(alpha: .65),
                                          borderRadius:
                                              const BorderRadius.vertical(
                                                  top: Radius.circular(4)))))
                          ]))),
            ],
            DemoCard(
                title: 'Collection status',
                icon: Icons.account_tree_outlined,
                child: Column(children: [
                  const DemoValueRow(
                      'Collector', 'No live collector running in demo'),
                  const DemoValueRow(
                      'Connection / freshness', 'Reported separately'),
                  DemoValueRow(
                      'Readings',
                      condition == 'Disconnected sample'
                          ? 'Stale sample · no live collector'
                          : 'Fixture values only'),
                  const DemoValueRow('Remote actions', 'None · monitoring-only')
                ]))
          ],
          const Text(
              'No SSH login, pairing, telemetry request or permission mutation occurs in this demo.',
              style: TextStyle(
                  fontSize: 12, color: DemoColors.muted, height: 1.8)),
        ]),
      ));
}
