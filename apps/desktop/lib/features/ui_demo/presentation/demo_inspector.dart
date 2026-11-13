import 'package:flutter/material.dart';

import 'demo_style.dart';

enum DemoPanel { files, terminal, changes }

class DemoInspector extends StatefulWidget {
  const DemoInspector(
      {super.key,
      required this.panel,
      required this.onPanel,
      required this.onClose});
  final DemoPanel panel;
  final ValueChanged<DemoPanel> onPanel;
  final VoidCallback onClose;

  @override
  State<DemoInspector> createState() => _DemoInspectorState();
}

class _DemoInspectorState extends State<DemoInspector> {
  String file = 'collector.py';
  static const collector = [
    '# Demonstration fixture; not production code.',
    'class Collector:',
    '    """One reader per monitored host."""',
    '',
    '    async def reconnect(self):',
    '        await self.stop_reader()',
    '        self.reader = await self.open_reader()',
    '        self.freshness = "waiting"',
    '',
    '    def capabilities(self):',
    '        return {',
    '            "monitoring": True,',
    '            "execution": False,',
    '            "workspace": False,',
    '        }',
  ];
  static const tests = [
    '# Demonstration fixture; not a test result.',
    'async def test_reconnect_has_one_reader():',
    '    collector = FixtureCollector()',
    '    await collector.reconnect()',
    '    await collector.reconnect()',
    '    assert collector.active_readers == 1',
    '',
    'def test_monitoring_does_not_grant_execution():',
    '    caps = FixtureCollector().capabilities()',
    '    assert caps["execution"] is False',
    '    assert caps["workspace"] is False',
  ];

  Widget codeLines(List<String> lines) => SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: SizedBox(
          width: 590,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            for (var i = 0; i < lines.length; i++)
              Padding(
                padding:
                    const EdgeInsets.symmetric(vertical: 4, horizontal: 12),
                child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                          width: 30,
                          child: Text('${i + 1}',
                              style: const TextStyle(
                                  fontFamily: 'OdessyMono',
                                  fontSize: 11,
                                  color: DemoColors.muted))),
                      Expanded(
                          child: SelectableText(lines[i],
                              style: TextStyle(
                                  fontFamily: 'OdessyMono',
                                  fontSize: 12,
                                  height: 1.6,
                                  color: lines[i].trim().startsWith('#')
                                      ? DemoColors.muted
                                      : lines[i].contains('False')
                                          ? DemoColors.amber
                                          : lines[i].contains('async') ||
                                                  lines[i].contains('def ')
                                              ? DemoColors.blue
                                              : DemoColors.ink))),
                    ]),
              ),
          ]),
        ),
      );

  @override
  Widget build(BuildContext context) => Material(
        color: DemoColors.sidebar,
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 6, 8),
            child: Row(children: [
              const Icon(Icons.space_dashboard_outlined,
                  size: 18, color: DemoColors.blue),
              const SizedBox(width: 8),
              const Expanded(
                  child: Text('Workspace',
                      style: TextStyle(fontWeight: FontWeight.w700))),
              IconButton(
                  tooltip: 'Close workspace panel',
                  onPressed: widget.onClose,
                  icon: const Icon(Icons.close, size: 18)),
            ]),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Row(children: [
              for (final panel in DemoPanel.values)
                Expanded(
                    child: Padding(
                  padding: const EdgeInsets.only(right: 4),
                  child: TextButton(
                    key: ValueKey('panel-${panel.name}'),
                    style: TextButton.styleFrom(
                        foregroundColor: widget.panel == panel
                            ? DemoColors.mint
                            : DemoColors.muted,
                        backgroundColor:
                            widget.panel == panel ? DemoColors.raised : null,
                        padding: const EdgeInsets.symmetric(
                            vertical: 14, horizontal: 4)),
                    onPressed: () => widget.onPanel(panel),
                    child: Text(
                        panel == DemoPanel.changes
                            ? 'Changes (1)'
                            : panel == DemoPanel.files
                                ? 'Files'
                                : 'Terminal',
                        style: const TextStyle(fontSize: 12)),
                  ),
                )),
            ]),
          ),
          const Padding(
              padding: EdgeInsets.all(16),
              child: DemoTag('Fixture workspace · no disk access',
                  icon: Icons.science_outlined)),
          const Divider(height: 1),
          Expanded(
              child: switch (widget.panel) {
            DemoPanel.files => _files(),
            DemoPanel.terminal => _terminal(),
            DemoPanel.changes => _changes(),
          }),
        ]),
      );

  Widget _files() => ListView(children: [
        const Padding(
            padding: EdgeInsets.fromLTRB(16, 18, 16, 8),
            child: Text('odessy / sample',
                style: TextStyle(fontSize: 12, color: DemoColors.muted))),
        for (final item in ['collector.py', 'test_collector.py', 'README.md'])
          ListTile(
            dense: true,
            selected: file == item,
            selectedTileColor: DemoColors.raised,
            selectedColor: DemoColors.ink,
            leading: Icon(
                item.endsWith('.py') ? Icons.code : Icons.description_outlined,
                size: 18,
                color:
                    item.endsWith('.py') ? DemoColors.blue : DemoColors.muted),
            title: Text(item, style: const TextStyle(fontSize: 12)),
            trailing: item == 'collector.py'
                ? const Text('M', style: TextStyle(color: DemoColors.amber))
                : null,
            onTap: () => setState(() => file = item),
          ),
        const Divider(height: 24),
        Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Text(file,
                style: const TextStyle(
                    fontSize: 12, fontWeight: FontWeight.w700))),
        const SizedBox(height: 16),
        codeLines(file == 'collector.py'
            ? collector
            : file == 'test_collector.py'
                ? tests
                : [
                    '# Collector review fixture',
                    '',
                    'These files exist only in this demo.',
                    'No real files are opened or changed.',
                    'SSH is monitoring-only.',
                  ]),
        const SizedBox(height: 24),
      ]);

  Widget _terminal() => ListView(padding: const EdgeInsets.all(18), children: [
        Row(children: [
          const Icon(Icons.terminal, size: 18, color: DemoColors.mint),
          const SizedBox(width: 8),
          const Expanded(
              child: Text('Recorded output',
                  style: TextStyle(fontWeight: FontWeight.w700))),
          IconButton(
              tooltip: 'About terminal output',
              onPressed: () => demoNotice(context,
                  'Static illustrative output. No process has been started.'),
              icon: const Icon(Icons.info_outline, size: 18))
        ]),
        const SizedBox(height: 12),
        const Text('Illustrative transcript, not an executed check.',
            style: TextStyle(color: DemoColors.amber, fontSize: 12)),
        const SizedBox(height: 24),
        const SelectableText(
            r'PS demo:\odessy> pytest sample -q'
            '\n\n'
            'collecting fixture tests…\n\n'
            '  reconnect closes old reader     PASS\n'
            '  monitoring keeps scope          PASS\n'
            '  revoked access hides metrics    PASS\n\n'
            '3 illustrative checks · 0.12s\n\n'
            r'PS demo:\odessy> '
            '\n\nNo shell is connected.',
            style: TextStyle(
                fontFamily: 'OdessyMono',
                fontSize: 12,
                height: 2,
                color: DemoColors.mint)),
        const SizedBox(height: 32),
        const DemoTag('Read-only display · input unavailable',
            icon: Icons.lock_outline),
      ]);

  Widget _changes() => ListView(padding: const EdgeInsets.all(16), children: [
        const Row(children: [
          Icon(Icons.difference_outlined, size: 18, color: DemoColors.blue),
          SizedBox(width: 8),
          Expanded(
              child: Text('collector.py',
                  style: TextStyle(fontWeight: FontWeight.w700))),
          Text('+3 −1',
              style: TextStyle(
                  color: DemoColors.mint,
                  fontFamily: 'OdessyMono',
                  fontSize: 12))
        ]),
        const SizedBox(height: 12),
        const Text('Proposed fixture patch',
            style: TextStyle(fontSize: 12, color: DemoColors.muted)),
        const SizedBox(height: 20),
        for (final (line, kind) in [
          ('@@ reconnect(self) @@', 0),
          ('  async def reconnect(self):', 0),
          ('-     self.open_reader()', -1),
          ('+     await self.stop_reader()', 1),
          ('+     self.reader = await self.open_reader()', 1),
          ('+     self.freshness = "waiting"', 1),
          ('', 0),
          ('  "execution": False,', 0),
          ('  "workspace": False,', 0),
        ])
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            color: kind == 1
                ? const Color(0xff1b4239)
                : kind == -1
                    ? const Color(0xff4a2d34)
                    : DemoColors.background,
            child: SelectableText(line,
                style: TextStyle(
                    fontFamily: 'OdessyMono',
                    fontSize: 11,
                    height: 1.7,
                    color: kind == 1
                        ? DemoColors.mint
                        : kind == -1
                            ? DemoColors.red
                            : DemoColors.muted)),
          ),
        const SizedBox(height: 20),
        const Text(
            'Review changes beside the conversation. Approvals remain a separate, explicit decision.',
            style:
                TextStyle(fontSize: 12, color: DemoColors.muted, height: 1.7)),
        const SizedBox(height: 16),
        const DemoTag('Preview only · nothing written',
            icon: Icons.visibility_outlined),
      ]);
}
