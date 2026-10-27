import 'package:flutter/material.dart';
import '../../../app/session_controller.dart';

class DevicePage extends StatelessWidget {
  const DevicePage(
      {super.key, required this.device, required this.controller, this.onPair});
  final String device;
  final DesktopSession controller;
  final VoidCallback? onPair;
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
                              : controller.connected
                                  ? 'Connected to Odessy service'
                                  : 'Not connected'),
                      _row('Node tool host',
                          'Controlled by backend configuration'),
                      _row('Secure host connections',
                          'Manage identity and access in connection tools'),
                    ]))),
        const SizedBox(height: 18),
        Card(
            child: Padding(
                padding: EdgeInsets.all(22),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Pairing & access',
                          style: TextStyle(fontWeight: FontWeight.w600)),
                      SizedBox(height: 12),
                      const Text(
                          'Connection tools provide identity verification, pairing and workspace grants.'),
                      SizedBox(height: 8),
                      Text(
                          'Connecting and granting access are explicit reviewed actions. Viewing this page does not enroll or authorize a computer.',
                          style: TextStyle(color: Colors.grey, fontSize: 12)),
                      SizedBox(height: 18),
                      OutlinedButton(
                          onPressed: onPair,
                          child: const Text('Open connection tools'))
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
