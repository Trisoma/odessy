import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../conversation/presentation/interaction_card.dart';
import '../../jobs/presentation/jobs_page.dart' show JobUsage;
import '../controllers/handoff_controller.dart';

const _flowLabels = {
  'document_to_code': 'Document to code',
  'code_to_review': 'Code to review',
};

/// Plain words for every run, step and transfer state the service reports.
String handoffStateLabel(Object? state) => switch (state) {
      'pending' => 'Not started',
      'queued' => 'Queued',
      'running' || 'running_step_1' || 'running_step_2' => 'Running',
      'awaiting_handoff' => 'Waiting for your decision',
      'handing_off' => 'Handing over',
      'cancelling' => 'Cancelling',
      'completed' => 'Completed',
      'cancelled' => 'Cancelled',
      'declined' => 'Declined',
      'failed' => 'Failed',
      'timed_out' => 'Timed out',
      'needs_attention' => 'Needs attention',
      String other => other,
      _ => 'Unknown',
    };

/// A transfer is the edge between the steps; its states mean something else.
String transferStateLabel(Object? state) => switch (state) {
      'waiting' => 'Waiting for step 1',
      'pending' => 'Waiting for your decision',
      'approved' => 'Approved',
      'delivered' => 'Delivered',
      'declined' => 'Declined',
      String other => other,
      _ => 'Unknown',
    };

class HandoffPage extends StatefulWidget {
  const HandoffPage({super.key, required this.controller});
  final HandoffController controller;
  @override
  State<HandoffPage> createState() => _HandoffPageState();
}

class _HandoffPageState extends State<HandoffPage> {
  final fromParticipant = TextEditingController(),
      fromTask = TextEditingController(),
      toParticipant = TextEditingController(),
      toTask = TextEditingController();
  String flow = 'document_to_code';

  @override
  void dispose() {
    for (final field in [fromParticipant, fromTask, toParticipant, toTask]) {
      field.dispose();
    }
    super.dispose();
  }

  bool get _complete => [fromParticipant, fromTask, toParticipant, toTask]
      .every((field) => field.text.trim().isNotEmpty);

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable:
          Listenable.merge([widget.controller, widget.controller.session]),
      builder: (context, _) {
        final c = widget.controller, s = c.session;
        final theme = Theme.of(context);
        final editable = c.available && !c.starting;
        final first =
            flow == 'document_to_code' ? 'writes a document' : 'writes code';
        final second = flow == 'document_to_code'
            ? 'implements it'
            : 'reviews it, read-only';
        return SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Center(
                child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 960),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Semantics(
                              header: true,
                              child: Text('Handoffs',
                                  style: theme.textTheme.headlineSmall)),
                          const SizedBox(height: 8),
                          const Text(
                              'One coding agent works, you review its output, '
                              'then a second agent continues from it in the same '
                              'isolated worktree. Starting and handing over each '
                              'need your approval. Nothing is merged for you.'),
                          if (!c.available)
                            const Padding(
                                padding: EdgeInsets.symmetric(vertical: 16),
                                child: Text(
                                    'Handoffs are unavailable for this connection.')),
                          if (c.error != null)
                            Padding(
                                padding:
                                    const EdgeInsets.symmetric(vertical: 12),
                                child: Text(c.error!,
                                    style: TextStyle(
                                        color: theme.colorScheme.error))),
                          if (c.startUncertain)
                            Card(
                                child: Padding(
                                    padding: const EdgeInsets.all(16),
                                    child: Column(
                                        crossAxisAlignment:
                                            CrossAxisAlignment.start,
                                        children: [
                                          const Text(
                                              'A previous start may have reached the service. '
                                              'Check session activity and the runs below before starting again.'),
                                          const SizedBox(height: 8),
                                          OutlinedButton(
                                              onPressed:
                                                  c.acknowledgeUncertainStart,
                                              child: const Text(
                                                  'I have checked the previous start')),
                                        ]))),
                          if (s.session?.pending.isNotEmpty == true)
                            for (final request in s.session!.pending)
                              InteractionCard(
                                  key: ValueKey(request['id']),
                                  entry: request,
                                  controller: s),
                          Card(
                              child: Padding(
                                  padding: const EdgeInsets.all(16),
                                  child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.stretch,
                                      children: [
                                        Text('Start a handoff',
                                            style: theme.textTheme.titleMedium),
                                        const SizedBox(height: 12),
                                        DropdownButtonFormField<String>(
                                            initialValue: flow,
                                            decoration: const InputDecoration(
                                                labelText: 'Flow'),
                                            items: [
                                              for (final entry
                                                  in _flowLabels.entries)
                                                DropdownMenuItem(
                                                    value: entry.key,
                                                    child: Text(entry.value))
                                            ],
                                            onChanged: editable
                                                ? (value) => setState(
                                                    () => flow = value!)
                                                : null),
                                        const SizedBox(height: 8),
                                        Text('Step 1 $first; step 2 $second.'),
                                        LayoutBuilder(builder: (context, box) {
                                          final steps = [
                                            _stepCard(
                                                '1 · Prepare',
                                                first,
                                                fromParticipant,
                                                fromTask,
                                                editable,
                                                1),
                                            _stepCard(
                                                '2 · Continue',
                                                second,
                                                toParticipant,
                                                toTask,
                                                editable,
                                                2),
                                          ];
                                          return box.maxWidth < 650
                                              ? Column(
                                                  crossAxisAlignment:
                                                      CrossAxisAlignment
                                                          .stretch,
                                                  children: steps)
                                              : Row(
                                                  crossAxisAlignment:
                                                      CrossAxisAlignment.start,
                                                  children: [
                                                      Expanded(child: steps[0]),
                                                      const SizedBox(width: 16),
                                                      Expanded(child: steps[1])
                                                    ]);
                                        }),
                                        const SizedBox(height: 16),
                                        Text('Review and request',
                                            style: theme.textTheme.titleSmall),
                                        const Text(
                                            'Review both steps before requesting approval. The service still owns every execution and transfer decision.'),
                                        const SizedBox(height: 12),
                                        Align(
                                            alignment: Alignment.centerLeft,
                                            child: FilledButton(
                                                onPressed: !editable ||
                                                        c.startUncertain ||
                                                        !_complete
                                                    ? null
                                                    : () => c.start(
                                                        flow: flow,
                                                        fromParticipant:
                                                            fromParticipant
                                                                .text,
                                                        fromTask: fromTask.text,
                                                        toParticipant:
                                                            toParticipant.text,
                                                        toTask: toTask.text),
                                                child: Text(c.starting
                                                    ? 'Waiting for approval or service'
                                                    : 'Request handoff'))),
                                        if (c.starting)
                                          const Text(
                                              'Approve the request when it appears. '
                                              'Approval needs the keyboard or this window; voice cannot approve it.'),
                                      ]))),
                          const SizedBox(height: 8),
                          Text('Session runs',
                              style: theme.textTheme.titleMedium),
                          if (c.available && c.runs.isEmpty)
                            const Padding(
                                padding: EdgeInsets.symmetric(vertical: 8),
                                child: Text(
                                    'No runs in this session yet. Runs end when the service restarts.')),
                          for (final run in c.runs)
                            ListTile(
                                selected: run['run_id'] == c.selectedId,
                                title: Text(
                                    '${_flowLabels[run['flow']]} · ${handoffStateLabel(run['state'])}'),
                                subtitle: Text(
                                    '${_participant(run, 0)} to ${_participant(run, 1)} · ${run['run_id']}'),
                                onTap: () => c.select(run['run_id'] as String)),
                          if (c.selected != null) _RunDetails(controller: c),
                        ]))));
      });

  static String _participant(Json run, int index) =>
      ((run['steps'] as List)[index] as Map)['participant'] as String;

  Widget _stepCard(
          String title,
          String description,
          TextEditingController participant,
          TextEditingController task,
          bool editable,
          int step) =>
      Card.outlined(
          child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(title, style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 8),
                    Text(description),
                    _field(participant, 'Step $step participant', editable),
                    const SizedBox(height: 12),
                    _field(task, 'Step $step task', editable, lines: true),
                  ])));

  Widget _field(TextEditingController field, String label, bool enabled,
          {bool lines = false}) =>
      TextField(
          controller: field,
          onChanged: (_) => setState(() {}),
          enabled: enabled,
          minLines: lines ? 2 : 1,
          maxLines: lines ? 5 : 1,
          decoration: InputDecoration(labelText: label));
}

class _RunDetails extends StatelessWidget {
  const _RunDetails({required this.controller});
  final HandoffController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller, run = c.selected!, theme = Theme.of(context);
    final steps = (run['steps'] as List).cast<Json>();
    final transfer = (run['transfers'] as List).first as Json;
    final offer = c.offered;
    final active = activeRunStates.contains(run['state']);
    return Card(
        child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(children: [
                    Expanded(
                        child: Text('Run · ${handoffStateLabel(run['state'])}',
                            style: theme.textTheme.titleMedium)),
                    IconButton(
                        tooltip: 'Reload run',
                        onPressed: c.available && !c.loading ? c.reload : null,
                        icon: const Icon(Icons.refresh)),
                  ]),
                  if (c.loading)
                    const LinearProgressIndicator(
                        semanticsLabel: 'Loading run'),
                  if (run['error'] is String)
                    Padding(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        child: Text('Stopped: ${run['error']}',
                            style: TextStyle(color: theme.colorScheme.error))),
                  const SizedBox(height: 8),
                  RunFlowView(run: run),
                  if (offer != null) ...[
                    const Divider(height: 32),
                    Text('Output waiting to be handed over',
                        style: theme.textTheme.titleSmall),
                    const SizedBox(height: 4),
                    Text(
                        '${offer['kind'] == 'diff' ? 'Code changes' : 'Document'} · '
                        '${offer['bytes']} bytes · goes to ${steps[1]['participant']}'),
                    SelectableText('SHA-256 ${offer['sha256']}',
                        style: const TextStyle(fontFamily: 'OdessyMono')),
                    const SizedBox(height: 8),
                    Container(
                        constraints: const BoxConstraints(maxHeight: 360),
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                            color: theme.colorScheme.surfaceContainerHighest,
                            borderRadius: BorderRadius.circular(8)),
                        child: SingleChildScrollView(
                            child: SelectableText(
                                (c.artifact?['text'] ?? offer['preview'])
                                    as String,
                                style: const TextStyle(
                                    fontFamily: 'OdessyMono')))),
                    if (c.artifact == null)
                      const Text('Showing the start of the output.'),
                    if (c.artifact != null)
                      const Text(
                          'Showing the full output. Text that looks like a secret is hidden here but is still handed over.'),
                    const SizedBox(height: 8),
                    Wrap(spacing: 8, runSpacing: 8, children: [
                      if (c.artifact == null)
                        OutlinedButton(
                            onPressed: c.available && !c.loadingArtifact
                                ? c.loadArtifact
                                : null,
                            child: Text(c.loadingArtifact
                                ? 'Loading full output'
                                : 'Show full output')),
                      FilledButton(
                          onPressed: !c.available ||
                                  c.continuing ||
                                  c.continueUncertain
                              ? null
                              : c.handOver,
                          child: Text(c.continuing
                              ? 'Waiting for approval or service'
                              : 'Hand over to ${steps[1]['participant']}')),
                    ]),
                    if (c.continueUncertain)
                      const Text(
                          'The last handoff was not confirmed. Reload the run before deciding again.'),
                    const Text(
                        'To stop here instead, cancel the run or decline the approval.'),
                  ],
                  if (transfer['path'] is String)
                    Padding(
                        padding: const EdgeInsets.only(top: 12),
                        child: Text(
                            'Handed over as ${transfer['path']} in the worktree.')),
                  if (run['worktree'] is String)
                    Padding(
                        padding: const EdgeInsets.only(top: 12),
                        child: SelectableText(
                            'Worktree: ${run['worktree']}\nIt stays after the run for you to review; nothing is merged automatically.')),
                  if (active)
                    Padding(
                        padding: const EdgeInsets.only(top: 12),
                        child: Align(
                            alignment: Alignment.centerLeft,
                            child: OutlinedButton(
                                onPressed: c.available &&
                                        !c.cancelling &&
                                        run['state'] != 'cancelling'
                                    ? c.cancel
                                    : null,
                                child: Text(c.cancelling
                                    ? 'Cancelling'
                                    : 'Cancel run')))),
                  if (active)
                    const Text('Cancellation does not undo completed work.'),
                ])));
  }
}

/// The two agents as nodes and the handed-over output as the edge between them.
/// Wide layouts read left to right; narrow ones top to bottom.
class RunFlowView extends StatelessWidget {
  const RunFlowView({super.key, required this.run});
  final Json run;

  @override
  Widget build(BuildContext context) {
    final steps = (run['steps'] as List).cast<Json>();
    final transfer = (run['transfers'] as List).first as Json;
    return LayoutBuilder(builder: (context, constraints) {
      final wide = constraints.maxWidth >= 640;
      final edge = _TransferEdge(transfer: transfer, wide: wide);
      final nodes = [
        _StepNode(step: steps[0], index: 1),
        edge,
        _StepNode(step: steps[1], index: 2),
      ];
      return wide
          ? IntrinsicHeight(
              child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                  Expanded(child: nodes[0]),
                  SizedBox(width: 168, child: nodes[1]),
                  Expanded(child: nodes[2]),
                ]))
          : Column(
              crossAxisAlignment: CrossAxisAlignment.stretch, children: nodes);
    });
  }
}

Color _tone(ColorScheme colors, Object? state) => switch (state) {
      'completed' || 'delivered' => colors.primary,
      'failed' ||
      'timed_out' ||
      'needs_attention' ||
      'declined' =>
        colors.error,
      _ => colors.outline,
    };

class _StepNode extends StatelessWidget {
  const _StepNode({required this.step, required this.index});
  final Json step;
  final int index;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context), colors = theme.colorScheme;
    final state = step['state'];
    final running = state == 'running' || state == 'queued';
    final progress = (step['progress'] as List? ?? const []).cast<String>();
    final changed = (step['changed_files'] as List? ?? const []).cast<String>();
    final mode = step['mode'] == 'patch' ? 'writes code' : 'read-only';
    // The step's final answer. A completed `code_to_review` run keeps the
    // reviewer's answer here, so it has to be drawn beside the progress.
    final result = step['result'];
    final answer = result is String && result.trim().isNotEmpty ? result : null;
    return Semantics(
        container: true,
        label: 'Step $index, ${step['participant']}, $mode, '
            '${handoffStateLabel(state)}',
        child: Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
                border: Border.all(
                    color: running ? colors.tertiary : _tone(colors, state),
                    width: running ? 2 : 1),
                borderRadius: BorderRadius.circular(12)),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Step $index · ${step['participant']}',
                  style: theme.textTheme.titleSmall),
              Text('$mode · ${handoffStateLabel(state)}'),
              if (running) ...[
                const SizedBox(height: 6),
                LinearProgressIndicator(semanticsLabel: 'Step $index running'),
              ],
              if (progress.isNotEmpty) ...[
                const SizedBox(height: 6),
                Text(progress.last,
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodySmall),
              ],
              if (changed.isNotEmpty) ...[
                const SizedBox(height: 6),
                Text(
                    'Changed: ${changed.take(5).join(', ')}'
                    '${changed.length > 5 ? ' and ${changed.length - 5} more' : ''}',
                    style: theme.textTheme.bodySmall),
              ],
              if (step['error'] is String) ...[
                const SizedBox(height: 6),
                Text(step['error'] as String,
                    style: theme.textTheme.bodySmall
                        ?.copyWith(color: colors.error)),
              ],
              if (step['usage'] is Map) JobUsage(usage: step['usage']),
              if (answer != null) ...[
                const SizedBox(height: 8),
                Text('Answer', style: theme.textTheme.labelLarge),
                const SizedBox(height: 4),
                Container(
                    width: double.infinity,
                    constraints: const BoxConstraints(maxHeight: 220),
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                        color: colors.surfaceContainerHighest,
                        borderRadius: BorderRadius.circular(8)),
                    child: SingleChildScrollView(
                        child: SelectableText(answer,
                            style: const TextStyle(fontFamily: 'OdessyMono')))),
              ],
            ])));
  }
}

class _TransferEdge extends StatelessWidget {
  const _TransferEdge({required this.transfer, required this.wide});
  final Json transfer;
  final bool wide;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context), colors = theme.colorScheme;
    final state = transfer['state'];
    final artifact = transfer['artifact'] as Json?;
    final color = state == 'pending' ? colors.tertiary : _tone(colors, state);
    final what = artifact == null
        ? 'Output'
        : artifact['kind'] == 'diff'
            ? 'Code changes'
            : 'Document';
    return Semantics(
        container: true,
        label: '$what from step 1 to step 2, ${transferStateLabel(state)}',
        child: Padding(
            padding: EdgeInsets.symmetric(
                horizontal: wide ? 8 : 0, vertical: wide ? 0 : 8),
            child:
                Column(mainAxisAlignment: MainAxisAlignment.center, children: [
              ExcludeSemantics(
                  child: Icon(wide ? Icons.arrow_forward : Icons.arrow_downward,
                      color: color, size: 32)),
              Text(what,
                  textAlign: TextAlign.center,
                  style: theme.textTheme.labelLarge),
              Text(transferStateLabel(state),
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodySmall?.copyWith(color: color)),
              if (artifact != null)
                Text(
                    '${artifact['bytes']} bytes · '
                    '${(artifact['sha256'] as String).substring(0, 12)}',
                    textAlign: TextAlign.center,
                    style: theme.textTheme.bodySmall),
            ])));
  }
}
