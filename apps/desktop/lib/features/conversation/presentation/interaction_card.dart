import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';

class InteractionCard extends StatefulWidget {
  const InteractionCard(
      {super.key, required this.entry, required this.controller});
  final Json entry;
  final DesktopSession controller;
  @override
  State<InteractionCard> createState() => _InteractionCardState();
}

class _InteractionCardState extends State<InteractionCard> {
  final answer = TextEditingController();
  Timer? timer;
  @override
  void initState() {
    super.initState();
    timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    timer?.cancel();
    answer.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final e = widget.entry,
        c = widget.controller,
        approval = e['kind'] == 'approval';
    final expiry = (e['expires_at'] as num?)?.toDouble() ?? 0;
    final remaining =
        (expiry - DateTime.now().millisecondsSinceEpoch / 1000).ceil();
    final allowed =
        (e['allowed_channels'] as List? ?? []).contains('authenticated_ui');
    final enabled = remaining > 0 && allowed && c.connected && !c.busy;
    return Padding(
        padding: const EdgeInsets.symmetric(vertical: 16),
        child: Card(
            child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(children: [
                        Icon(
                            approval
                                ? Icons.shield_outlined
                                : Icons.help_outline,
                            size: 18,
                            color: Colors.amber),
                        const SizedBox(width: 10),
                        Expanded(
                            child: Text(
                                approval
                                    ? 'Approval required'
                                    : 'I need one detail…',
                                style: const TextStyle(
                                    fontWeight: FontWeight.w600))),
                        Text(remaining > 0 ? '${remaining}s' : 'Expired',
                            style: const TextStyle(
                                fontSize: 11, color: Colors.grey))
                      ]),
                      const SizedBox(height: 14),
                      Text(approval
                          ? (e['authoritative_prompt'] ??
                              e['prompt'] ??
                              'Review this exact action.') as String
                          : e['question'] as String),
                      const SizedBox(height: 12),
                      if (approval)
                        for (final fact in approvalFacts(e))
                          Padding(
                            padding: const EdgeInsets.only(bottom: 6),
                            child: Row(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  SizedBox(
                                      width: 104,
                                      child: Text(fact.$1,
                                          style: const TextStyle(
                                              fontSize: 11,
                                              color: Colors.grey))),
                                  Expanded(
                                      child: SelectableText(fact.$2,
                                          style:
                                              const TextStyle(fontSize: 12))),
                                ]),
                          ),
                      if (approval)
                        ExpansionTile(
                            title: const Text('Details',
                                style: TextStyle(fontSize: 11)),
                            children: [
                              SelectableText(
                                  const JsonEncoder.withIndent('  ').convert(e),
                                  style: const TextStyle(
                                      fontSize: 11, fontFamily: 'monospace'))
                            ]),
                      const SizedBox(height: 14),
                      if (!allowed)
                        const Text(
                            'This request does not permit this response channel.'),
                      if (approval)
                        Wrap(spacing: 10, children: [
                          OutlinedButton(
                              onPressed: enabled
                                  ? () => c.resolve(e, approved: false)
                                  : null,
                              child: const Text('Deny')),
                          FilledButton(
                              onPressed: enabled
                                  ? () => c.resolve(e, approved: true)
                                  : null,
                              child: const Text('Approve once')),
                        ])
                      else ...[
                        Wrap(spacing: 8, children: [
                          for (final choice in e['choices'] as List? ?? [])
                            OutlinedButton(
                                onPressed: enabled
                                    ? () =>
                                        c.resolve(e, answer: choice as String)
                                    : null,
                                child: Text(choice as String))
                        ]),
                        if (e['allow_free_text'] == true)
                          Padding(
                              padding: const EdgeInsets.only(top: 10),
                              child: Row(children: [
                                Expanded(
                                    child: TextField(
                                        controller: answer,
                                        enabled: enabled,
                                        decoration: const InputDecoration(
                                            labelText: 'Your answer'))),
                                const SizedBox(width: 10),
                                FilledButton(
                                    onPressed: enabled
                                        ? () {
                                            if (answer.text.trim().isNotEmpty) {
                                              c.resolve(e,
                                                  answer: answer.text.trim());
                                            }
                                          }
                                        : null,
                                    child: const Text('Answer'))
                              ])),
                      ],
                      const SizedBox(height: 12),
                      Text(
                          approval
                              ? 'One-use authorization · exact action only · completed work is not undone by Stop'
                              : 'Your answer supplies information. It does not grant permission.',
                          style: const TextStyle(
                              fontSize: 10, color: Colors.grey)),
                    ]))));
  }
}

/// Human-readable authorization facts, in review order. Missing values are
/// rendered as explicitly unknown rather than dropped, so an incomplete
/// request cannot look complete.
List<(String, String)> approvalFacts(Json e) {
  String text(Object? value) {
    if (value == null) return 'not reported';
    if (value is List) {
      return value.isEmpty
          ? 'none reported'
          : value.map((v) => '$v').join(', ');
    }
    if (value is Map) {
      return value.isEmpty
          ? 'none reported'
          : value.entries
              .map((entry) => '${entry.key}=${entry.value}')
              .join(', ');
    }
    final raw = '$value';
    return raw.isEmpty ? 'not reported' : raw;
  }

  return [
    ('Action', text(e['operation'] ?? e['tool_name'] ?? e['tool'])),
    (
      'Execution target',
      text(e['target_identity'] ?? e['execution_target'] ?? e['host'])
    ),
    ('Permission scope', text(e['permission_scopes'] ?? e['scopes'])),
    ('Risk', text(e['risk'])),
    (
      'Exact arguments',
      e.containsKey('arguments') ? text(e['arguments']) : 'not reported'
    ),
  ];
}
