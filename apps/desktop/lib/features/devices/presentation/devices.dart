import 'package:flutter/material.dart';
import '../../../app/session_controller.dart';

class DevicePage extends StatelessWidget {
  const DevicePage({super.key, required this.device, required this.controller});
  final String device;
  final DesktopSession controller;
  @override
  Widget build(BuildContext context) =>
      ListView(padding: const EdgeInsets.all(32), children: [
        Row(children: [
          Icon(
              device == 'Jetson'
                  ? Icons.memory_outlined
                  : Icons.computer_outlined,
              size: 35,
              color: Theme.of(context).colorScheme.primary),
          const SizedBox(width: 16),
          Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(device, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 5),
            Text(
                controller.api.isDemo
                    ? 'Demo device · illustrative state'
                    : controller.api.identity,
                style: const TextStyle(color: Colors.grey, fontSize: 12))
          ])
        ]),
        const SizedBox(height: 25),
        const Text(
            'Inspecting a device does not change the conversation coordinator or execution target.',
            style: TextStyle(color: Colors.grey, fontSize: 12)),
        const SizedBox(height: 25),
        Card(
            child: Padding(
                padding: const EdgeInsets.all(22),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Runtime',
                          style: TextStyle(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 14),
                      _row(
                          'Python coordinator',
                          controller.api.isDemo
                              ? 'Demo fixture'
                              : 'Connected through session API'),
                      _row('Node tool host',
                          'Controlled by backend configuration'),
                      _row('Rust secure transport', 'Management API pending'),
                    ]))),
        const SizedBox(height: 18),
        const Card(
            child: Padding(
                padding: EdgeInsets.all(22),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Pairing & access',
                          style: TextStyle(fontWeight: FontWeight.w600)),
                      SizedBox(height: 12),
                      Text('Pairing management API is not available yet.'),
                      SizedBox(height: 8),
                      Text(
                          'Existing CLI pairing remains available. This preview never edits trust databases, host grants, or identity keys.',
                          style: TextStyle(color: Colors.grey, fontSize: 12)),
                      SizedBox(height: 18),
                      OutlinedButton(
                          onPressed: null, child: Text('Pair device'))
                    ]))),
        const SizedBox(height: 18),
        const Card(
            child: Padding(
                padding: EdgeInsets.all(22),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Voice',
                          style: TextStyle(fontWeight: FontWeight.w600)),
                      SizedBox(height: 12),
                      Text(
                          'Use the existing voice CLI. Desktop microphone lifecycle integration is planned for v0.5.0.',
                          style: TextStyle(color: Colors.grey, fontSize: 12))
                    ]))),
      ]);
  Widget _row(String name, String value) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(children: [
        SizedBox(
            width: 175,
            child: Text(name, style: const TextStyle(fontSize: 12))),
        Expanded(
            child: Text(value,
                style: const TextStyle(fontSize: 12, color: Colors.grey)))
      ]));
}
