import 'package:flutter/material.dart';
import 'demo_style.dart';

/// Isolated review fixture. No handoff, worktree or provider is started here.
class DemoHandoffs extends StatefulWidget {
  const DemoHandoffs({super.key});
  @override
  State<DemoHandoffs> createState() => _DemoHandoffsState();
}

class _DemoHandoffsState extends State<DemoHandoffs> {
  bool reviewFlow = false, outputExpanded = false;
  String decision = '';

  Widget step(int number, String title, String description, String person,
          String task, IconData icon) =>
      DemoCard(
          title: title,
          icon: icon,
          description: description,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            DemoTag('STEP $number', color: DemoColors.blue),
            const SizedBox(height: 20),
            Text('Participant', style: Theme.of(context).textTheme.labelMedium),
            const SizedBox(height: 8),
            DropdownButtonFormField<String>(
              key: ValueKey('$number-$reviewFlow'),
              initialValue: person,
              decoration: const InputDecoration(
                  prefixIcon: Icon(Icons.person_outline, size: 18)),
              items: [person, 'Choose another fixture participant']
                  .map((value) =>
                      DropdownMenuItem(value: value, child: Text(value)))
                  .toList(),
              onChanged: (_) {},
            ),
            const SizedBox(height: 20),
            Text('Task', style: Theme.of(context).textTheme.labelMedium),
            const SizedBox(height: 8),
            TextFormField(
              key: ValueKey('task-$number-$reviewFlow'),
              initialValue: task,
              minLines: 3,
              maxLines: 5,
              style: const TextStyle(fontSize: 13, height: 1.6),
              decoration:
                  const InputDecoration(contentPadding: EdgeInsets.all(14)),
            ),
          ]));

  Widget get review => DemoCard(
      title: 'Review the transfer',
      icon: Icons.fact_check_outlined,
      description: 'You decide what the next participant receives.',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        DemoTag(
            decision.isEmpty
                ? 'Awaiting your decision · fixture'
                : '$decision · fixture only',
            color: DemoColors.amber,
            icon: Icons.shield_outlined),
        const SizedBox(height: 24),
        const Text('Collector reconnect plan',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
        const SizedBox(height: 10),
        const Text(
            'Close the previous reader before reconnecting. Keep monitoring read-only and add a focused regression.',
            style:
                TextStyle(fontSize: 13, height: 1.8, color: DemoColors.muted)),
        const SizedBox(height: 20),
        const DemoValueRow('From', 'Planning participant'),
        const DemoValueRow('To', 'Implementation participant'),
        const DemoValueRow('Workspace', 'Isolated worktree · example'),
        const DemoValueRow('Output identity', 'Exact artifact hash · example'),
        const SizedBox(height: 12),
        OutlinedButton.icon(
            onPressed: () => setState(() => outputExpanded = !outputExpanded),
            icon: Icon(
                outputExpanded ? Icons.unfold_less : Icons.description_outlined,
                size: 17),
            label: Text(outputExpanded
                ? 'Hide sample output'
                : 'Inspect sample output')),
        if (outputExpanded) ...[
          const SizedBox(height: 12),
          const SelectableText(
              '1. Join the old collector.\n2. Open exactly one reader.\n3. Mark freshness as waiting.\n4. Prove reconnect does not duplicate collectors.',
              style: TextStyle(
                  fontFamily: 'OdessyMono', fontSize: 12, height: 1.8)),
        ],
        const SizedBox(height: 24),
        Wrap(spacing: 10, runSpacing: 10, children: [
          FilledButton.icon(
              onPressed: decision.isEmpty
                  ? () => setState(() => decision = 'Transfer reviewed')
                  : null,
              icon: const Icon(Icons.check, size: 17),
              label: const Text('Approve demo transfer')),
          OutlinedButton(
              onPressed: decision.isEmpty
                  ? () => setState(() => decision = 'Transfer declined')
                  : null,
              child: const Text('Decline demo')),
        ]),
        const SizedBox(height: 16),
        const Text(
            'Preview only. No agent runs, no authority is granted and nothing is merged.',
            style:
                TextStyle(fontSize: 11, height: 1.7, color: DemoColors.muted)),
      ]));

  @override
  Widget build(BuildContext context) =>
      ListView(padding: const EdgeInsets.all(28), children: [
        const Align(
            alignment: Alignment.centerLeft,
            child: DemoTag('Handoff design · fixture only',
                icon: Icons.science_outlined)),
        const SizedBox(height: 18),
        const Text('One step forward. Your decision between.',
            style: TextStyle(
                fontSize: 28, fontWeight: FontWeight.w700, letterSpacing: -.5)),
        const SizedBox(height: 10),
        const Text(
            'Give each participant a clear task. Review the first output before the next step begins.',
            style:
                TextStyle(color: DemoColors.muted, fontSize: 14, height: 1.8)),
        const SizedBox(height: 24),
        Wrap(spacing: 10, runSpacing: 10, children: [
          ChoiceChip(
              label: const Text('Plan then implement'),
              selected: !reviewFlow,
              onSelected: (_) => setState(() {
                    reviewFlow = false;
                    decision = '';
                  })),
          ChoiceChip(
              label: const Text('Implement then review'),
              selected: reviewFlow,
              onSelected: (_) => setState(() {
                    reviewFlow = true;
                    decision = '';
                  })),
        ]),
        const SizedBox(height: 24),
        LayoutBuilder(builder: (context, constraints) {
          final stages = Column(children: [
            step(
                1,
                reviewFlow ? 'Implement the change' : 'Write a focused plan',
                'A clear input and a reviewable output.',
                reviewFlow
                    ? 'Implementation participant'
                    : 'Planning participant',
                reviewFlow
                    ? 'Implement the reviewed collector reconnect fix.'
                    : 'Describe the reconnect fix, its boundaries and regression checks.',
                reviewFlow ? Icons.code : Icons.edit_note_outlined),
            const Padding(
                padding: EdgeInsets.only(bottom: 16),
                child: Row(children: [
                  Icon(Icons.shield_outlined,
                      color: DemoColors.amber, size: 18),
                  SizedBox(width: 10),
                  Expanded(
                      child: Text('Your review connects the two steps',
                          style: TextStyle(
                              color: DemoColors.amber, fontSize: 12))),
                ])),
            step(
                2,
                reviewFlow ? 'Review the result' : 'Implement the plan',
                'The approved output becomes explicit context.',
                reviewFlow
                    ? 'Review participant'
                    : 'Implementation participant',
                reviewFlow
                    ? 'Check the change and test evidence. Report risks before merging.'
                    : 'Follow the approved plan. Preserve permissions and return the test evidence.',
                reviewFlow ? Icons.fact_check_outlined : Icons.code),
          ]);
          return constraints.maxWidth < 900
              ? Column(children: [stages, review])
              : Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Expanded(flex: 6, child: stages),
                  const SizedBox(width: 24),
                  Expanded(flex: 5, child: review),
                ]);
        }),
      ]);
}
