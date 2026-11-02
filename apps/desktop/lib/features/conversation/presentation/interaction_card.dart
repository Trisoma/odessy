import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/session_controller.dart';
import '../../../app/theme.dart';

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
  String? sessionId;

  @override
  void initState() {
    super.initState();
    sessionId = widget.controller.session?.id;
    timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void didUpdateWidget(covariant InteractionCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    final nextSession = widget.controller.session?.id;
    if (sessionId != nextSession ||
        oldWidget.controller != widget.controller ||
        jsonEncode(oldWidget.entry) != jsonEncode(widget.entry)) {
      answer.clear();
    }
    sessionId = nextSession;
  }

  @override
  void dispose() {
    timer?.cancel();
    answer.dispose();
    super.dispose();
  }

  String _format(Object? value) => value is String
      ? value
      : const JsonEncoder.withIndent('  ').convert(value);

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final e = widget.entry, c = widget.controller;
          final approval = e['kind'] == 'approval',
              question = e['kind'] == 'question';
          final rawExpiry = e['expires_at'];
          final expiry = rawExpiry is num && rawExpiry.isFinite
              ? rawExpiry.toDouble()
              : 0.0;
          final remaining =
              (expiry - DateTime.now().millisecondsSinceEpoch / 1000).ceil();
          final allowed = e['allowed_channels'] is List &&
              (e['allowed_channels'] as List).contains('authenticated_ui');
          final current =
              c.session?.pending.any((entry) => identical(entry, e)) ?? false;
          final String? unavailable = !current
              ? 'This request is no longer pending.'
              : !approval && !question
                  ? 'This request type is unavailable.'
                  : remaining <= 0
                      ? 'This request has expired. Wait for a new request.'
                      : !allowed
                          ? 'This request does not permit this response channel.'
                          : !c.connected
                              ? 'Reconnect to the session service before responding.'
                              : c.stopping
                                  ? 'Stopping the current turn…'
                                  : c.busy
                                      ? 'Session is busy. Please wait.'
                                      : null;
          final enabled = unavailable == null;
          return Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Card(
                  child: Padding(
                padding: const EdgeInsets.all(OdessyTokens.spaceLarge),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Wrap(
                          spacing: 16,
                          runSpacing: 8,
                          crossAxisAlignment: WrapCrossAlignment.center,
                          children: [
                            Row(mainAxisSize: MainAxisSize.min, children: [
                              Icon(
                                  approval
                                      ? Icons.shield_outlined
                                      : Icons.help_outline,
                                  size: 20,
                                  color: Theme.of(context).colorScheme.primary),
                              const SizedBox(width: 8),
                              Flexible(
                                  child: Semantics(
                                      header: true,
                                      child: Text(
                                          approval
                                              ? 'Approval required'
                                              : question
                                                  ? 'One detail needed'
                                                  : 'Request unavailable',
                                          style: Theme.of(context)
                                              .textTheme
                                              .titleMedium))),
                            ]),
                            Text(
                                remaining > 0
                                    ? 'Expires in ${remaining}s'
                                    : 'Expired',
                                style: Theme.of(context).textTheme.bodySmall),
                          ]),
                      const SizedBox(height: 16),
                      Text(approval
                          ? _format(e['authoritative_prompt'] ??
                              e['prompt'] ??
                              'Review this exact action.')
                          : _format(
                              e['question'] ?? 'Review the service request.')),
                      const SizedBox(height: 16),
                      if (approval) ...[
                        for (final field in approvalFacts(e).take(4))
                          _detail(context, field.$1, field.$2),
                        if (!e.containsKey('arguments'))
                          _detail(context, 'Exact arguments', 'not reported'),
                        if (e.containsKey('arguments')) ...[
                          const SizedBox(height: 12),
                          Text('Exact arguments',
                              style: Theme.of(context).textTheme.labelLarge),
                          const SizedBox(height: 8),
                          Container(
                              padding: const EdgeInsets.all(16),
                              decoration: BoxDecoration(
                                  color: Theme.of(context)
                                      .colorScheme
                                      .surfaceContainerLow,
                                  borderRadius: BorderRadius.circular(
                                      OdessyTokens.radiusSmall)),
                              child: SelectableText(_format(e['arguments']),
                                  style:
                                      Theme.of(context).textTheme.bodySmall)),
                        ],
                      ],
                      ExpansionTile(
                          title: const Text('Details'),
                          tilePadding: EdgeInsets.zero,
                          expandedCrossAxisAlignment:
                              CrossAxisAlignment.stretch,
                          children: [
                            SelectableText(_format(e),
                                style: Theme.of(context).textTheme.bodySmall)
                          ]),
                      if (unavailable != null)
                        Padding(
                            padding: const EdgeInsets.only(bottom: 12),
                            child: Text(unavailable)),
                      if (approval)
                        Wrap(spacing: 10, runSpacing: 8, children: [
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
                        ]),
                      if (question) ...[
                        Wrap(spacing: 8, runSpacing: 8, children: [
                          for (final choice in (e['choices'] is List
                                  ? e['choices'] as List
                                  : const [])
                              .whereType<String>())
                            OutlinedButton(
                                onPressed: enabled
                                    ? () => c.resolve(e, answer: choice)
                                    : null,
                                child: Text(choice)),
                        ]),
                        if (e['allow_free_text'] == true) ...[
                          const SizedBox(height: 12),
                          TextField(
                              controller: answer,
                              enabled: enabled,
                              minLines: 1,
                              maxLines: 4,
                              onChanged: (_) => setState(() {}),
                              decoration: const InputDecoration(
                                  labelText: 'Your answer')),
                          const SizedBox(height: 12),
                          Align(
                              alignment: Alignment.centerLeft,
                              child: FilledButton(
                                  onPressed:
                                      enabled && answer.text.trim().isNotEmpty
                                          ? () => c.resolve(e,
                                              answer: answer.text.trim())
                                          : null,
                                  child: const Text('Answer'))),
                        ],
                      ],
                      const SizedBox(height: 12),
                      Text(
                          approval
                              ? 'One-use authorization for this exact action. Stop does not undo completed work.'
                              : 'Your answer supplies information. It does not grant permission.',
                          style: Theme.of(context).textTheme.bodySmall),
                    ]),
              )));
        },
      );

  Widget _detail(BuildContext context, String label, String value) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: LayoutBuilder(builder: (context, box) {
          final heading =
              Text(label, style: Theme.of(context).textTheme.bodySmall);
          final content = SelectableText(value);
          if (box.maxWidth < 480) {
            return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [heading, const SizedBox(height: 4), content]);
          }
          return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(width: 150, child: heading),
            const SizedBox(width: 12),
            Expanded(child: content),
          ]);
        }),
      );
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
