import 'dart:convert';
import 'package:flutter/material.dart';
import '../../conversation/presentation/interaction_card.dart';
import '../controllers/jobs_controller.dart';

class JobsPage extends StatefulWidget {
  const JobsPage({super.key, required this.controller});
  final JobsController controller;
  @override
  State<JobsPage> createState() => _JobsPageState();
}

class _JobsPageState extends State<JobsPage> {
  final participant = TextEditingController(), task = TextEditingController();
  String mode = 'advice';
  @override
  void dispose() {
    participant.dispose();
    task.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable:
          Listenable.merge([widget.controller, widget.controller.session]),
      builder: (context, _) {
        final c = widget.controller, s = c.session;
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
                              child: Text('Jobs',
                                  style: Theme.of(context)
                                      .textTheme
                                      .headlineSmall)),
                          const SizedBox(height: 8),
                          const Text(
                              'Jobs belong to the connected session. Submission requires service approval.'),
                          if (!c.available)
                            const Padding(
                                padding: EdgeInsets.symmetric(vertical: 16),
                                child: Text(
                                    'Job management is unavailable for this connection.')),
                          if (c.error != null)
                            Padding(
                                padding:
                                    const EdgeInsets.symmetric(vertical: 12),
                                child: Text(c.error!)),
                          if (c.submissionUncertain)
                            Card(
                                child: Padding(
                                    padding: const EdgeInsets.all(16),
                                    child: Column(
                                        crossAxisAlignment:
                                            CrossAxisAlignment.start,
                                        children: [
                                          const Text(
                                              'A previous submission may have reached the service. Review session activity and jobs before submitting again.'),
                                          OutlinedButton(
                                              onPressed: c
                                                  .acknowledgeUncertainSubmission,
                                              child: const Text(
                                                  'I have checked the previous submission')),
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
                                        Text('Submit a job',
                                            style: Theme.of(context)
                                                .textTheme
                                                .titleMedium),
                                        const SizedBox(height: 12),
                                        TextField(
                                            controller: participant,
                                            onChanged: (_) => setState(() {}),
                                            enabled:
                                                c.available && !c.submitting,
                                            decoration: const InputDecoration(
                                                labelText:
                                                    'Participant profile')),
                                        TextField(
                                            controller: task,
                                            onChanged: (_) => setState(() {}),
                                            enabled:
                                                c.available && !c.submitting,
                                            minLines: 2,
                                            maxLines: 5,
                                            decoration: const InputDecoration(
                                                labelText: 'Task')),
                                        DropdownButtonFormField<String>(
                                            initialValue: mode,
                                            decoration: const InputDecoration(
                                                labelText: 'Mode'),
                                            items: const [
                                              DropdownMenuItem(
                                                  value: 'advice',
                                                  child: Text('Advice')),
                                              DropdownMenuItem(
                                                  value: 'patch',
                                                  child: Text('Patch'))
                                            ],
                                            onChanged:
                                                c.available && !c.submitting
                                                    ? (value) => setState(
                                                        () => mode = value!)
                                                    : null),
                                        const SizedBox(height: 12),
                                        Align(
                                            alignment: Alignment.centerLeft,
                                            child: FilledButton(
                                                onPressed: !c.available ||
                                                        c.submitting ||
                                                        c.submissionUncertain ||
                                                        participant.text
                                                            .trim()
                                                            .isEmpty ||
                                                        task.text.trim().isEmpty
                                                    ? null
                                                    : () => c.submit(
                                                        participant:
                                                            participant.text,
                                                        task: task.text,
                                                        mode: mode),
                                                child: Text(c.submitting
                                                    ? 'Waiting for approval or service'
                                                    : 'Request job'))),
                                        if (c.submitting)
                                          const Text(
                                              'Review the approval request when it appears. Do not submit again while the result is uncertain.'),
                                      ]))),
                          Row(children: [
                            Expanded(
                                child: Text('Session jobs',
                                    style: Theme.of(context)
                                        .textTheme
                                        .titleMedium)),
                            IconButton(
                                tooltip: 'Refresh jobs',
                                onPressed: c.available && !c.loading
                                    ? () => c.refresh()
                                    : null,
                                icon: const Icon(Icons.refresh))
                          ]),
                          if (c.loading)
                            const LinearProgressIndicator(
                                semanticsLabel: 'Loading jobs'),
                          if (c.available && !c.loading && c.jobs.isEmpty)
                            const Text('No jobs reported.'),
                          for (final job in c.jobs)
                            ListTile(
                                title: Text(
                                    '${job['participant']} · ${job['mode']}'),
                                subtitle:
                                    Text('${job['state']} · ${job['job_id']}'),
                                onTap: c.loading
                                    ? null
                                    : () => c.inspect(job['job_id'] as String)),
                          if (c.nextCursor != null)
                            Align(
                                alignment: Alignment.centerLeft,
                                child: OutlinedButton(
                                    onPressed: c.loading
                                        ? null
                                        : () => c.refresh(more: true),
                                    child: const Text('Load more'))),
                          if (c.selected != null)
                            Card(
                                child: Padding(
                                    padding: const EdgeInsets.all(16),
                                    child: Column(
                                        crossAxisAlignment:
                                            CrossAxisAlignment.stretch,
                                        children: [
                                          Text('Job details',
                                              style: Theme.of(context)
                                                  .textTheme
                                                  .titleMedium),
                                          JobUsage(usage: c.selected!['usage']),
                                          SelectableText(
                                              const JsonEncoder.withIndent('  ')
                                                  .convert(c.selected)),
                                          if (c.selectedCancellationUncertain)
                                            Align(
                                                alignment: Alignment.centerLeft,
                                                child: OutlinedButton(
                                                    onPressed: c.loading ||
                                                            !c.available
                                                        ? null
                                                        : () => c.inspect(
                                                            c.selected![
                                                                    'job_id']
                                                                as String),
                                                    child: const Text(
                                                        'Reload job status'))),
                                          if (const {'queued', 'running'}
                                              .contains(c.selected!['state']))
                                            Align(
                                                alignment: Alignment.centerLeft,
                                                child: OutlinedButton(
                                                    onPressed: c.cancelling ||
                                                            c.loading ||
                                                            !c.available ||
                                                            c
                                                                .selectedCancellationUncertain
                                                        ? null
                                                        : () => c.cancel(
                                                            c.selected![
                                                                    'job_id']
                                                                as String),
                                                    child: const Text(
                                                        'Cancel job'))),
                                          const Text(
                                              'Cancellation does not undo completed work.'),
                                        ]))),
                        ]))));
      });
}

/// Provider-reported coding CLI figures are separate from the API ledger.
class JobUsage extends StatelessWidget {
  const JobUsage({super.key, required this.usage});
  final Object? usage;

  String _count(Object? value) =>
      value is int && value >= 0 ? value.toString() : 'Not reported';

  @override
  Widget build(BuildContext context) {
    final data = usage is Map ? usage as Map : const {};
    if (data['source'] != 'coding_cli') {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 12),
        child: Text('No coding CLI usage report is available.'),
      );
    }
    final cost = data['cost'];
    final currency = data['currency'];
    final validCurrency =
        currency is String && RegExp(r'^[A-Za-z]{1,16}$').hasMatch(currency);
    final costText = cost is num && cost.isFinite && cost >= 0
        ? '$cost ${validCurrency ? currency : '(currency not reported)'}'
        : 'Not reported';
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const SizedBox(height: 12),
      const Text('Coding CLI reported usage'),
      Text('Input tokens: ${_count(data['input_tokens'])}'),
      Text('Cached input tokens: ${_count(data['cached_input_tokens'])}'),
      Text('Output tokens: ${_count(data['output_tokens'])}'),
      Text('Reported cost: $costText'),
      const Text(
          'Separate from the API ledger and API budget; provider reporting may be incomplete.'),
      const SizedBox(height: 12),
    ]);
  }
}
