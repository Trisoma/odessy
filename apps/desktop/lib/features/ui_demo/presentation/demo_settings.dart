import 'package:flutter/material.dart';

import '../../settings/presentation/settings.dart' show SettingsSections;
import 'demo_style.dart';

/// Reviews the supported settings groups without constructing live controllers.
class DemoSettings extends StatefulWidget {
  const DemoSettings({super.key});
  @override
  State<DemoSettings> createState() => _DemoSettingsState();
}

class _DemoSettingsState extends State<DemoSettings> {
  String section = SettingsSections.general;
  bool dirty = false;
  final Map<String, String> values = {};
  final Map<String, bool> switches = {};
  static const icons = [
    Icons.tune,
    Icons.hub_outlined,
    Icons.hearing_outlined,
    Icons.record_voice_over_outlined,
    Icons.mic_none,
    Icons.shield_outlined,
    Icons.code,
    Icons.inventory_2_outlined
  ];
  static const descriptions = {
    SettingsSections.general: 'Make room for the way you work.',
    SettingsSections.models:
        'Choose how Odessy thinks. Keep credentials with their owner.',
    SettingsSections.stt:
        'Turn speech into text, with explicit audio ownership.',
    SettingsSections.tts: 'Give responses a voice without changing your model.',
    SettingsSections.voice:
        'Keep listening, speaking and consent clearly separate.',
    SettingsSections.devices:
        'Connections establish identity. Permissions establish access.',
    SettingsSections.advanced:
        'Understand runtime ownership before changing it.',
    SettingsSections.installation:
        'Know what is installed and what needs attention.',
  };

  Widget field(String label, String initial, {String? help}) => Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label,
              style:
                  const TextStyle(fontSize: 12, fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          TextFormField(
              key: ValueKey('$section-$label'),
              initialValue: values['$section-$label'] ?? initial,
              onChanged: (value) => setState(() {
                    values['$section-$label'] = value;
                    dirty = true;
                  }),
              style: const TextStyle(fontSize: 13),
              decoration: InputDecoration(
                  contentPadding: const EdgeInsets.all(14),
                  labelText: label,
                  floatingLabelBehavior: FloatingLabelBehavior.never)),
          if (help != null)
            Padding(
                padding: const EdgeInsets.only(top: 7),
                child: Text(help,
                    style: const TextStyle(
                        fontSize: 11, color: DemoColors.muted, height: 1.6))),
        ]),
      );

  Widget toggle(String label, String detail, bool initial) => SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: Text(label, style: const TextStyle(fontSize: 13)),
        subtitle: Text(detail,
            style: const TextStyle(
                fontSize: 11, color: DemoColors.muted, height: 1.6)),
        value: switches['$section-$label'] ?? initial,
        onChanged: (value) => setState(() {
          switches['$section-$label'] = value;
          dirty = true;
        }),
      );

  Widget note(String text) => Padding(
      padding: const EdgeInsets.only(top: 6, bottom: 8),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Icon(Icons.info_outline, size: 17, color: DemoColors.blue),
        const SizedBox(width: 9),
        Expanded(
            child: Text(text,
                style: const TextStyle(
                    fontSize: 12, color: DemoColors.muted, height: 1.7)))
      ]));

  List<Widget> content() => switch (section) {
        SettingsSections.general => [
            DemoCard(
                title: 'Appearance',
                icon: Icons.palette_outlined,
                description:
                    'A calm workspace with visible actions and readable content.',
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const DemoValueRow('Theme', 'Ocean · dark design preview',
                          color: DemoColors.mint),
                      const DemoValueRow(
                          'Navigation', 'Assistant / Workspaces / Devices'),
                      const DemoValueRow('Text and motion',
                          'Scalable text · visible keyboard focus'),
                      note(
                          'Appearance here is a design proposal. No OS preference is changed.'),
                    ])),
            DemoCard(
                title: 'Collaboration',
                icon: Icons.forum_outlined,
                description:
                    'Limits apply to new sessions; the backend remains authoritative.',
                child: Column(children: [
                  field('Maximum consultations', '2',
                      help: 'Demo draft · production range: 0–32.'),
                  note(
                      'Editing this sample does not enable delegation or alter a running session.')
                ]))
          ],
        SettingsSections.models => [
            DemoCard(
                title: 'Model profiles',
                icon: Icons.hub_outlined,
                description:
                    'Select profiles in the composer for the next turn.',
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Wrap(spacing: 8, runSpacing: 8, children: [
                        DemoTag('Qwen3 · local fixture',
                            icon: Icons.computer, color: DemoColors.mint),
                        DemoTag('Custom + Harness retained',
                            icon: Icons.account_tree_outlined,
                            color: DemoColors.blue)
                      ]),
                      const SizedBox(height: 22),
                      field('Profile name', 'Local assistant'),
                      field('Model identifier', 'qwen3:8b'),
                      field('Endpoint', 'http://localhost:11434',
                          help:
                              'Illustrative endpoint only. No provider probe or download is performed.'),
                    ])),
            DemoCard(
                title: 'Credentials and ownership',
                icon: Icons.key_outlined,
                child: Column(children: [
                  const DemoValueRow('Credential values',
                      'Never shown or stored by this demo'),
                  const DemoValueRow('Credential handling',
                      'References only · secrets not shown'),
                  note(
                      'Live integration must preserve revisions, validation and credential-reference handling.')
                ]))
          ],
        SettingsSections.stt => [
            DemoCard(
                title: 'Speech recognition',
                icon: Icons.hearing_outlined,
                description:
                    'Recognition is an optional component, separate from microphone permission.',
                child: Column(children: [
                  field('Recognition model', 'whisper-small · fixture'),
                  field('Language', 'Auto-detect'),
                  const DemoValueRow('Audio capture', 'Not connected',
                      color: DemoColors.amber),
                  const DemoValueRow(
                      'Model assets', 'Illustrative · no download'),
                  note(
                      'This page does not record, transcribe or install speech assets.')
                ]))
          ],
        SettingsSections.tts => [
            DemoCard(
                title: 'Speech synthesis',
                icon: Icons.record_voice_over_outlined,
                description:
                    'Inspect voice configuration before enabling playback.',
                child: Column(children: [
                  field('Speech provider', 'Local speech adapter · fixture'),
                  field('Voice', 'Default English voice'),
                  const DemoValueRow(
                      'Output device', 'System default · not opened'),
                  const DemoValueRow(
                      'Voice preview', 'Unavailable in this design demo',
                      color: DemoColors.amber),
                  note('No speech engine, audio device or provider is started.')
                ]))
          ],
        SettingsSections.voice => [
            DemoCard(
                title: 'Voice session',
                icon: Icons.mic_none,
                description:
                    'Starting voice is explicit and scoped to the active conversation.',
                child: Column(children: [
                  const DemoValueRow(
                      'Current voice state', 'Off · no audio connection',
                      color: DemoColors.mint),
                  toggle(
                      'Enable voice component · demo draft',
                      'In-memory preference only. This does not start listening.',
                      false),
                  const Divider(),
                  const DemoValueRow('Microphone permission', 'Not requested'),
                  const DemoValueRow(
                      'Voice lifecycle', 'Explicit start · stop when leaving'),
                  note(
                      'Leaving a live conversation must release its voice owner. Reconnection must not silently restart listening.')
                ]))
          ],
        SettingsSections.devices => [
            DemoCard(
                title: 'Connection and capability',
                icon: Icons.devices_outlined,
                description:
                    'A selected computer is never an implicit execution target.',
                child: const Column(children: [
                  DemoValueRow('This computer', 'Local coordinator · fixture'),
                  DemoValueRow('Jetson', 'SSH / monitoring only',
                      color: DemoColors.blue),
                  DemoValueRow('Paired transport',
                      'Retained · separate identity and grants'),
                  DemoValueRow('SSH execution / workspace', 'Unavailable',
                      color: DemoColors.amber),
                ])),
            DemoCard(
                title: 'Tool decisions',
                icon: Icons.shield_outlined,
                child: Column(children: [
                  const DemoValueRow('Approval authority',
                      'Session/request-bound backend broker'),
                  const DemoValueRow(
                      'Telemetry access', 'Separate read-only permission'),
                  note(
                      'This demo cannot change SSH trust, grant permissions, pair a host or revoke live access.')
                ]))
          ],
        SettingsSections.advanced => [
            DemoCard(
                title: 'Session and runtime',
                icon: Icons.account_tree_outlined,
                description:
                    'Inspect the active context before starting a new session.',
                child: const Column(children: [
                  DemoValueRow('Selected engine', 'Custom · fixture'),
                  DemoValueRow('Alternative engine', 'Harness'),
                  DemoValueRow('Selectable engines', 'Custom / Harness'),
                  DemoValueRow('Coordinator', 'This computer · fixture'),
                  DemoValueRow('Changed settings', 'Apply to new sessions'),
                  DemoValueRow('Service connection', 'Not connected in demo'),
                ])),
            DemoCard(
                title: 'Diagnostics',
                icon: Icons.manage_search,
                child: Column(children: [
                  const DemoValueRow(
                      'Runtime inventory', 'Read-only metadata in live mode'),
                  const DemoValueRow(
                      'Configuration values', 'Not loaded in this demo'),
                  note(
                      'No runtime environment variables or secret values are read for this preview.')
                ]))
          ],
        _ => [
            DemoCard(
                title: 'Installation overview',
                icon: Icons.inventory_2_outlined,
                description:
                    'A design preview of installation status, not an installation receipt.',
                child: const Column(children: [
                  DemoValueRow('Application', 'Odessy v0.5.0 · source demo'),
                  DemoValueRow('Installed version', 'Not inspected',
                      color: DemoColors.amber),
                  DemoValueRow('Installation root', 'Not accessed'),
                  DemoValueRow(
                      'Data and settings', 'Existing user files untouched'),
                ])),
            DemoCard(
                title: 'Maintenance',
                icon: Icons.build_outlined,
                child: Column(children: [
                  note(
                      'Real repair, uninstall and update operations require the existing lifecycle plan and explicit review.'),
                  const DemoValueRow('Installer / updater', 'Not started'),
                  const DemoValueRow('Release gates',
                      'Signing, installed checks and human review remain separate'),
                  const SizedBox(height: 8),
                  const DemoTag(
                      'No build, install, update or uninstall actions',
                      color: DemoColors.blue,
                      icon: Icons.lock_outline)
                ]))
          ],
      };

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, size) {
        final rail = size.maxWidth >= 850;
        Widget menu() =>
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              const Padding(
                  padding: EdgeInsets.fromLTRB(16, 12, 8, 18),
                  child: Text('Preferences',
                      style: TextStyle(fontSize: 12, color: DemoColors.muted))),
              for (var i = 0; i < SettingsSections.all.length; i++)
                Padding(
                    padding: const EdgeInsets.only(bottom: 5),
                    child: ListTile(
                      key: ValueKey('settings-${SettingsSections.all[i]}'),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                      selected: section == SettingsSections.all[i],
                      selectedTileColor: DemoColors.raised,
                      selectedColor: DemoColors.mint,
                      dense: true,
                      leading: Icon(icons[i], size: 18),
                      minLeadingWidth: 18,
                      horizontalTitleGap: 10,
                      title: Text(SettingsSections.all[i],
                          style: const TextStyle(fontSize: 12)),
                      onTap: () =>
                          setState(() => section = SettingsSections.all[i]),
                    )),
              const Padding(
                  padding: EdgeInsets.all(16),
                  child: Text('Design preview\nEdits stay in memory.',
                      style: TextStyle(
                          fontSize: 11, color: DemoColors.muted, height: 1.8))),
            ]);
        return Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          if (rail)
            SizedBox(
                width: 230,
                child: Material(
                    color: DemoColors.sidebar,
                    child: Padding(
                        padding: const EdgeInsets.all(12),
                        child: SingleChildScrollView(child: menu())))),
          Expanded(
              child: Center(
                  child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 1000),
            child: ListView(
                key: ValueKey('settings-content-$section'),
                padding: EdgeInsets.all(rail ? 36 : 20),
                children: [
                  if (!rail) ...[
                    DropdownButtonFormField<String>(
                        initialValue: section,
                        isExpanded: true,
                        decoration: const InputDecoration(
                            labelText: 'Settings section'),
                        items: SettingsSections.all
                            .map((s) => DropdownMenuItem(
                                value: s,
                                child: Text(s,
                                    style: const TextStyle(fontSize: 13))))
                            .toList(),
                        onChanged: (s) {
                          if (s != null) setState(() => section = s);
                        }),
                    const SizedBox(height: 24)
                  ],
                  const Align(
                      alignment: Alignment.centerLeft,
                      child: DemoTag('Settings · fixture values',
                          icon: Icons.science_outlined)),
                  const SizedBox(height: 16),
                  Text(section,
                      style: const TextStyle(
                          fontSize: 29,
                          fontWeight: FontWeight.w700,
                          letterSpacing: -.6)),
                  const SizedBox(height: 9),
                  Text(descriptions[section]!,
                      style: const TextStyle(
                          color: DemoColors.muted, fontSize: 14, height: 1.8)),
                  const SizedBox(height: 26),
                  ...content(),
                  Wrap(
                      spacing: 12,
                      runSpacing: 10,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        FilledButton.icon(
                            onPressed: dirty
                                ? () {
                                    setState(() => dirty = false);
                                    demoNotice(context,
                                        'Demo draft retained in memory only. Nothing was saved to Odessy.');
                                  }
                                : null,
                            icon: const Icon(Icons.check, size: 17),
                            label: const Text('Keep demo draft')),
                        Text(
                            dirty
                                ? 'Unsaved demo edits · no backend writes'
                                : 'No pending demo edits',
                            style: const TextStyle(
                                fontSize: 11, color: DemoColors.muted)),
                      ]),
                ]),
          ))),
        ]);
      });
}
