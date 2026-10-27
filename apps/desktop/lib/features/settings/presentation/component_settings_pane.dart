import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/setup/native_picker.dart';
import '../../setup/controllers/model_catalog_controller.dart';
import '../../setup/presentation/model_picker.dart';
import '../controllers/component_settings_controller.dart';

/// Independent component editor. Saving patches only this section on the backend.
class ComponentSettingsPane extends StatefulWidget {
  const ComponentSettingsPane(
      {super.key,
      required this.controller,
      required this.section,
      this.picker = const UnavailablePicker()});
  final ComponentSettingsController controller;
  final String section;
  final NativePicker picker;
  @override
  State<ComponentSettingsPane> createState() => _ComponentSettingsPaneState();
}

class _ComponentSettingsPaneState extends State<ComponentSettingsPane> {
  final fields = <String, TextEditingController>{};
  late final ModelCatalogController catalog;
  bool initialised = false, enabled = false;
  String provider = 'ollama', role = 'combined';
  ComponentSettingsController get c => widget.controller;
  bool get locked => c.busy || c.inspectionRequired;
  @override
  void initState() {
    super.initState();
    catalog = ModelCatalogController(c.backend);
    c.addListener(_refresh);
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      c.activeSection = widget.section;
      if (c.snapshot.isEmpty) await c.load();
      _refresh();
    });
  }

  void _refresh() {
    if (!mounted) return;
    if (!c.busy && c.snapshot['selection'] is Map) {
      final selection = c.snapshot['selection'] as Map;
      final voice = selection['voice'] as Map? ?? {};
      final model = selection['model'] as Map? ?? {};
      final data = widget.section == 'llm'
          ? model
          : widget.section == 'host'
              ? {
                  'role': selection['role'],
                  'workspace': c.snapshot['workspace']
                }
              : voice;
      final draft = c.draft(widget.section, {
        for (final key in _keys) key: '${data[key] ?? ''}',
        if (widget.section == 'llm')
          'provider': '${model['provider'] ?? 'ollama'}',
        if (widget.section == 'host')
          'role': '${selection['role'] ?? 'combined'}',
        if (widget.section == 'voice') 'enabled': voice['enabled'] == true,
      });
      for (final key in _keys) {
        final field = fields.putIfAbsent(key, () => TextEditingController());
        final value = '${draft.values[key] ?? ''}';
        if (field.text != value) field.text = value;
      }
      provider = '${draft.values['provider'] ?? 'ollama'}';
      role = '${draft.values['role'] ?? 'combined'}';
      enabled = draft.values['enabled'] == true;
      initialised = true;
    }
    setState(() {});
  }

  List<String> get _keys => switch (widget.section) {
        'llm' => ['name', 'endpoint', 'credential_env'],
        'stt' => [
            'stt_provider',
            'whisper_bin',
            'whisper_model',
            'stt_executable',
            'stt_model'
          ],
        'tts' => [
            'tts_provider',
            'kokoro_model',
            'kokoro_voices',
            'tts_executable',
            'tts_model'
          ],
        'host' => ['workspace'],
        _ => []
      };
  @override
  void dispose() {
    c.removeListener(_refresh);
    catalog.dispose();
    for (final f in fields.values) {
      f.dispose();
    }
    super.dispose();
  }

  Json _patch() => widget.section == 'llm'
      ? {
          'provider': provider,
          for (final key in _keys) key: fields[key]!.text.trim()
        }
      : widget.section == 'voice'
          ? {'enabled': enabled}
          : widget.section == 'host'
              ? {
                  'role': role,
                  if (fields['workspace']!.text.trim().isNotEmpty)
                    'workspace': fields['workspace']!.text.trim()
                }
              : {for (final key in _keys) key: fields[key]!.text};

  void _remember() => c.edit(widget.section, _patch());
  Future<void> _save() async {
    _remember();
    await c.save(widget.section, _patch());
  }

  Widget _field(String key, {bool browse = false}) => Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: TextField(
          key: ValueKey('component-$key'),
          controller: fields[key],
          enabled: !locked,
          decoration: InputDecoration(
              labelText: key.replaceAll('_', ' '),
              suffixIcon: browse
                  ? IconButton(
                      tooltip: 'Browse ${key.replaceAll('_', ' ')}',
                      onPressed: locked
                          ? null
                          : () async {
                              final path = widget.section == 'host'
                                  ? await widget.picker
                                      .pickDirectory(initial: fields[key]!.text)
                                  : await widget.picker
                                      .pickFile(initial: fields[key]!.text);
                              if (mounted && path != null) {
                                fields[key]!.text = path;
                                _remember();
                              }
                            },
                      icon: const Icon(Icons.folder_open))
                  : null),
          onChanged: (_) => _remember()));
  @override
  Widget build(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(
            switch (widget.section) {
              'llm' => 'LLM',
              'stt' => 'Speech to text (STT)',
              'tts' => 'Text to speech (TTS)',
              'host' => 'Host connectivity',
              _ => 'Voice pipeline'
            },
            style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 12),
        const Text(
            'Save this component independently. Runtime changes apply after restarting Odessy. Saving does not run inference, download models or grant permissions.'),
        const SizedBox(height: 20),
        if (c.busy && !initialised) const LinearProgressIndicator(),
        if (!initialised && !c.busy)
          OutlinedButton(
              onPressed: c.load,
              child: const Text('Retry loading configuration')),
        if (initialised) ...[
          if (widget.section == 'llm') ...[
            DropdownButtonFormField<String>(
                initialValue: provider,
                decoration: const InputDecoration(labelText: 'Provider'),
                items: [
                  for (final name in [
                    'ollama',
                    'lfm',
                    'deepseek',
                    'openai',
                    'claude',
                    'qwen',
                    'kimi',
                    'custom'
                  ])
                    DropdownMenuItem(value: name, child: Text(name))
                ],
                onChanged: locked
                    ? null
                    : (value) => setState(() {
                          provider = value!;
                          fields['name']!.clear();
                          fields['endpoint']!.clear();
                          _remember();
                        })),
            const SizedBox(height: 12),
            _field('endpoint'),
            _field('credential_env'),
            ModelPicker(
                catalog: catalog,
                provider: provider,
                endpoint: fields['endpoint']!.text.trim(),
                credential: fields['credential_env']!.text.trim(),
                value: fields['name']!,
                fieldKey: const ValueKey('component-name'),
                enabled: !locked,
                onChanged: _remember),
            const Text(
                'Credentials are environment-variable references only; never enter an API key.'),
          ] else if (widget.section == 'voice')
            SwitchListTile(
                title: const Text('Enable the voice pipeline'),
                subtitle: const Text(
                    'STT and TTS can be configured first, while voice remains disabled.'),
                value: enabled,
                onChanged: locked
                    ? null
                    : (v) {
                        enabled = v;
                        _remember();
                      })
          else if (widget.section == 'host') ...[
            DropdownButtonFormField<String>(
                initialValue: role,
                decoration:
                    const InputDecoration(labelText: 'This computer’s role'),
                items: [
                  for (final name in ['combined', 'coordinator', 'execution'])
                    DropdownMenuItem(value: name, child: Text(name))
                ],
                onChanged: locked
                    ? null
                    : (value) {
                        role = value!;
                        _remember();
                      }),
            const SizedBox(height: 12),
            _field('workspace', browse: true),
            const Text(
                'Pairing and workspace grants are separate explicit actions. Changing role does not enroll or authorize a host.'),
          ] else ...[
            const Text(
                'Use whisper.cpp / kokoro defaults, or a trusted module:factory provider. Only this component’s paths are checked.'),
            const SizedBox(height: 12),
            for (final key in _keys)
              _field(key, browse: !key.endsWith('_provider')),
            const Text(
                'Saving paths does not enable voice or configure the other speech component.'),
          ],
          const SizedBox(height: 18),
          if (!c.dirty && !c.busy && !c.inspectionRequired)
            const Text('No unsaved changes in this section.'),
          Wrap(spacing: 12, children: [
            FilledButton(
                onPressed:
                    c.busy || c.inspectionRequired || !c.dirty ? null : _save,
                child: const Text('Save this section')),
            OutlinedButton(
                onPressed: c.busy
                    ? null
                    : () async {
                        if (c.dirty) {
                          final discard = await showDialog<bool>(
                              context: context,
                              builder: (context) => AlertDialog(
                                      title: const Text(
                                          'Discard this section’s draft?'),
                                      content: const Text(
                                          'Reload replaces only these edits with saved values.'),
                                      actions: [
                                        TextButton(
                                            onPressed: () =>
                                                Navigator.pop(context, false),
                                            child: const Text('Keep editing')),
                                        TextButton(
                                            onPressed: () =>
                                                Navigator.pop(context, true),
                                            child: const Text(
                                                'Discard and reload'))
                                      ]));
                          if (discard != true || !mounted) return;
                        }
                        await c.reloadSection(widget.section);
                      },
                child: const Text('Reload saved values'))
          ]),
          if (c.inspectionRequired)
            OutlinedButton(
                onPressed: c.busy ? null : c.load,
                child: const Text('Inspect saved values')),
        ],
        if (c.status.isNotEmpty)
          Padding(
              padding: const EdgeInsets.only(top: 14), child: Text(c.status)),
        if (c.error.isNotEmpty)
          Padding(
              padding: const EdgeInsets.only(top: 14),
              child: Text(c.error,
                  style:
                      TextStyle(color: Theme.of(context).colorScheme.error))),
      ]);
}
