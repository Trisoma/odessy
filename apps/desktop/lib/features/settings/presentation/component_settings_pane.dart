import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/setup/native_picker.dart';
import '../../setup/controllers/model_catalog_controller.dart';
import '../../setup/presentation/model_picker.dart';
import '../controllers/component_settings_controller.dart';
import '../controllers/audio_inventory_controller.dart';
import '../controllers/voice_discovery_controller.dart';
import 'provider_probe_panel.dart';
import 'model_credential_panel.dart';

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
  late final VoiceDiscoveryController discovery;
  late final AudioInventoryController inventory;
  final detected = <String>{};
  bool initialised = false, enabled = false, customDevice = false;
  String provider = 'ollama', role = 'combined';
  ComponentSettingsController get c => widget.controller;
  bool get locked => c.busy || c.inspectionRequired;
  @override
  void initState() {
    super.initState();
    catalog = ModelCatalogController(c.backend);
    discovery = VoiceDiscoveryController(c.backend, section: widget.section);
    inventory = AudioInventoryController(c.backend);
    c.addListener(_refresh);
    discovery.addListener(_refresh);
    inventory.addListener(_refresh);
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
    _autoDetect();
    setState(() {});
  }

  /// Detection runs once per section opening, and only once the draft is populated.
  /// It is observational: drafts, saved values and other components are untouched.
  void _autoDetect() {
    if (!initialised) return;
    if (_assetSections.contains(widget.section) &&
        detected.add('assets-${widget.section}')) {
      discovery.detect(_probeSelection());
    }
    if (widget.section == 'voice' && detected.add('devices')) {
      inventory.detect(_probeSelection());
    }
  }

  static const _assetSections = {'stt', 'tts', 'voice'};

  /// Re-run the helper's field checks after a path was chosen or typed. The helper
  /// owns the filesystem: this layer never stats a path.
  void _recheckPaths() {
    if (_assetSections.contains(widget.section) && initialised && !c.busy) {
      discovery.validate(_scopedValidationSelection());
    }
  }

  /// Exactly the fields on screen, so a check can only ever return verdicts for this
  /// section's own files.
  Json _scopedValidationSelection() => {
        'role': '${(c.snapshot['selection'] as Map?)?['role'] ?? 'combined'}',
        'voice': {
          for (final key in _sectionAssetKeys) key: fields[key]?.text ?? ''
        },
      };

  String? _errorFor(String key) =>
      c.fieldErrors[key] ?? discovery.blockingIssue(key);

  String? _warningFor(String key) => discovery.warningIssue(key);

  /// Only the asset fields this section actually shows, for the selected provider.
  ///
  /// A blocking verdict on another component's file belongs to that component: an
  /// unusable Whisper binary must never stop a TTS or Voice save.
  Set<String> get _sectionAssetKeys {
    if (widget.section == 'stt' || widget.section == 'tts') {
      return {
        for (final key in _visibleKeys)
          if (!key.endsWith('_provider')) key
      };
    }
    if (widget.section == 'voice') return {'wake_model'};
    return const <String>{};
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
        'voice' => [
            'audio_backend',
            'audio_input_device',
            'audio_output_device',
            'wake_model'
          ],
        _ => []
      };
  @override
  void dispose() {
    c.removeListener(_refresh);
    discovery.removeListener(_refresh);
    inventory.removeListener(_refresh);
    catalog.dispose();
    discovery.dispose();
    inventory.dispose();
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
          ? {
              'enabled': enabled,
              for (final key in _keys) key: fields[key]!.text
            }
          : widget.section == 'host'
              ? {
                  'role': role,
                  'workspace': fields['workspace']!.text.trim()
                }
              : {for (final key in _keys) key: fields[key]!.text};

  void _remember() => c.edit(widget.section, _patch());
  Json _probeSelection() {
    final value = Map<String, dynamic>.from(c.snapshot['selection'] as Map);
    if (widget.section == 'llm') {
      value['model'] = _patch();
    } else if (widget.section != 'host') {
      value['voice'] = {
        ...Map<String, dynamic>.from(value['voice'] as Map),
        ..._patch()
      };
    }
    return value;
  }

  Future<void> _save() async {
    _remember();
    await c.save(widget.section, _patch());
  }

  /// Every edit stays inside this section. A provider change replaces the asset set,
  /// so stale suggestions are dropped and the section is scanned again.
  void _onFieldChanged(String key) {
    _remember();
    if (key.endsWith('_provider')) {
      discovery.invalidate();
      detected.remove('assets-${widget.section}');
      _autoDetect();
    } else if (_sectionAssetKeys.contains(key)) {
      discovery.selectionChanged(_scopedValidationSelection());
    }
  }

  Widget _field(String key, {bool browse = false}) => Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: TextField(
          key: ValueKey('component-$key'),
          controller: fields[key],
          enabled: !locked,
          decoration: InputDecoration(
              errorText: _errorFor(key),
              labelText: key.replaceAll('_', ' '),
              suffixIcon: browse ? _browseButton(key) : null),
          onChanged: (_) => _onFieldChanged(key)));
  String _label(String key) => key.replaceAll('_', ' ');

  /// A short form that keeps the filename visible on a narrow window; the full path
  /// stays in the text field and the tooltip once chosen.
  String _shortPath(String path) {
    final parts = path.split(RegExp(r'[\\/]')).where((part) => part.isNotEmpty).toList();
    if (parts.length <= 2) return path;
    return '…/${parts.sublist(parts.length - 2).join('/')}';
  }

  Widget _browseButton(String key) => Tooltip(
      message: 'Browse ${_label(key)}',
      child: TextButton(
          style: TextButton.styleFrom(
            minimumSize: const Size(96, 44),
            padding: const EdgeInsets.symmetric(horizontal: 12),
            foregroundColor: Theme.of(context).colorScheme.primary,
            side: BorderSide(color: Theme.of(context).colorScheme.outline),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
          ),
          onPressed: locked
              ? null
              : () async {
                  // Cancelling Browse changes nothing. The host section browses a
                  // directory; every speech asset is a file.
                  final path = widget.section == 'host'
                      ? await widget.picker.pickDirectory(initial: fields[key]!.text)
                      : await widget.picker.pickFile(initial: fields[key]!.text);
                  if (mounted && path != null) {
                    fields[key]!.text = path;
                    _remember();
                    _recheckPaths();
                  }
                },
          child: const Text('Browse…')));

  /// A path field with Browse, a picker over detected files and field-specific
  /// validation. Choosing a candidate changes only this field and never saves.
  Widget _pathField(String key) {
    final candidates = discovery.of(key);
    return Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          TextField(
              key: ValueKey('component-$key'),
              controller: fields[key],
              enabled: !locked,
              decoration: InputDecoration(
                  errorText: _errorFor(key),
                  helperText: _warningFor(key),
                  labelText: _label(key),
                  suffixIcon: _browseButton(key)),
              onChanged: (_) => _onFieldChanged(key),
              onSubmitted: (_) => _recheckPaths()),
          if (candidates.isNotEmpty) ...[
            const SizedBox(height: 6),
            Tooltip(
                message: 'Existing files found by detection. None of them were tested.',
                child: PopupMenuButton<String>(
                    key: ValueKey('component-$key-candidates'),
                    enabled: !locked,
                    tooltip: 'Detected ${_label(key)}',
                    onSelected: (value) {
                      fields[key]!.text = value;
                      _remember();
                      _recheckPaths();
                    },
                    itemBuilder: (context) => [
                          for (final path in candidates)
                            PopupMenuItem(
                                value: path,
                                child: Text(_shortPath(path),
                                    overflow: TextOverflow.ellipsis))
                        ],
                    child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                        decoration: BoxDecoration(
                            border: Border.all(color: Theme.of(context).colorScheme.outline),
                            borderRadius: BorderRadius.circular(8)),
                        child: Text('Detected (${candidates.length})')))),
          ],
        ]));
  }

  /// One observation strip per section: scan once on open, then explicitly again.
  Widget _detectionStrip() => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(spacing: 12, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
              OutlinedButton(
                  onPressed: locked || discovery.busy
                      ? null
                      : () => discovery.detect(_probeSelection()),
                  child: Text(discovery.found
                      ? 'Detect existing components again'
                      : 'Detect existing components')),
              if (discovery.busy) const Text('Scanning existing files…'),
            ]),
            if (discovery.error.isNotEmpty)
              Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(discovery.error,
                      style: TextStyle(color: Theme.of(context).colorScheme.error))),
            if (discovery.partial)
              const Text('Scan stopped early, so some locations were not searched. Browse still works.'),
            if (!discovery.busy && discovery.error.isEmpty && discovery.reason.isNotEmpty)
              Text(discovery.reason),
            const SizedBox(height: 12),
          ]);

  /// A device selector for one direction. Saved identifiers are never rewritten:
  /// a value that is not in the inventory stays selected and is labelled unavailable.
  Widget _deviceField(String key, String label, String direction) {
    final unique = <String, Json>{
      for (final device in inventory.forDirection(direction)) '${device['id']}': device
    };
    final options = unique.values.toList();
    final saved = fields[key]!.text;
    final unavailable = saved.isNotEmpty && !unique.containsKey(saved);
    final names = <String, int>{};
    for (final device in options) {
      final name = '${device['name'] ?? ''}';
      names[name] = (names[name] ?? 0) + 1;
    }
    String labelFor(Json device) {
      final name = '${device['name'] ?? device['id']}';
      final suffix = device['is_default'] == true ? ' · system default' : '';
      if ((names[name] ?? 0) <= 1) return '$name$suffix';
      final id = '${device['id']}';
      final api = id.contains('::') ? id.split('::').first : id;
      return '$name — $api$suffix';
    }

    return Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (customDevice)
            TextField(
                key: ValueKey('component-$key'),
                controller: fields[key],
                enabled: !locked,
                decoration: InputDecoration(
                    errorText: _errorFor(key),
                    labelText: '$label identifier',
                    helperText: 'Passed unchanged to the selected backend. Nothing was opened.'),
                onChanged: (_) => _remember())
          else
            // Stable key for tests and a11y; the inner key changes so a reloaded or
            // externally changed identifier is reflected instead of going stale.
            KeyedSubtree(
                key: ValueKey('component-$key'),
                child: DropdownButtonFormField<String>(
                key: ValueKey('component-$key-${inventory.backendName}-$saved'),
                initialValue: saved,
                isExpanded: true,
                decoration: InputDecoration(
                    labelText: label,
                    errorText: _errorFor(key),
                    helperText: options.isEmpty
                        ? 'No $direction device was detected. Refresh devices, or use Advanced.'
                        : null),
                items: [
                  const DropdownMenuItem(value: '', child: Text('System default')),
                  for (final device in options)
                    DropdownMenuItem(
                        value: '${device['id']}',
                        child: Text(labelFor(device), overflow: TextOverflow.ellipsis)),
                  if (unavailable)
                    DropdownMenuItem(
                        value: saved,
                        child: const Text('Previously selected device — unavailable',
                            overflow: TextOverflow.ellipsis)),
                ],
                onChanged: locked
                    ? null
                    : (value) {
                        if (value == null) return;
                        fields[key]!.text = value;
                        _remember();
                      })),
          if (unavailable)
            Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('Previously selected device — unavailable: $saved',
                    key: ValueKey('component-$key-unavailable'),
                    style: TextStyle(color: Theme.of(context).colorScheme.error))),
        ]));
  }

  Widget _backendField() {
    final current = fields['audio_backend']!.text;
    final value = const {'auto', 'command', 'native'}.contains(current) ? current : 'auto';
    return DropdownButtonFormField<String>(
        key: ValueKey('component-audio_backend-$value'),
        initialValue: value,
        isExpanded: true,
        decoration: InputDecoration(
            labelText: 'Audio backend', errorText: _errorFor('audio_backend')),
        items: const [
          DropdownMenuItem(
              value: 'auto',
              child: Text('Auto — native on Windows/macOS, ALSA commands on Linux',
                  overflow: TextOverflow.ellipsis)),
          DropdownMenuItem(
              value: 'command', child: Text('Command — arecord / aplay')),
          DropdownMenuItem(
              value: 'native', child: Text('Native — PortAudio devices')),
        ],
        onChanged: locked
            ? null
            : (next) {
                if (next == null) return;
                fields['audio_backend']!.text = next;
                // Device results belong to the backend that produced them. Saved
                // identifiers are kept verbatim and the running backend is untouched.
                inventory.invalidate();
                _remember();
                inventory.detect(_probeSelection());
              });
  }

  Widget _deviceControls() => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(spacing: 12, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
              OutlinedButton(
                  onPressed: locked || inventory.busy
                      ? null
                      : () => inventory.detect(_probeSelection()),
                  child: Text(inventory.busy ? 'Refreshing devices…' : 'Refresh devices')),
              FilterChip(
                  key: const ValueKey('component-custom-device'),
                  selected: customDevice,
                  label: const Text('Advanced: custom device identifier'),
                  onSelected: locked ? null : (value) => setState(() => customDevice = value)),
            ]),
            if (inventory.error.isNotEmpty)
              Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(inventory.error,
                      style: TextStyle(color: Theme.of(context).colorScheme.error))),
            if (inventory.message.isNotEmpty) Text(inventory.message),
            const SizedBox(height: 12),
          ]);

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
                decoration: InputDecoration(
                    labelText: 'Provider',
                    errorText: c.fieldErrors['provider']),
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
            if (!{'ollama', 'lfm'}.contains(provider))
              ModelCredentialPanel(backend: c.backend, provider: provider,
                endpoint: fields['endpoint']!.text.trim(),
                reference: fields['credential_env']!.text.trim()),
            const Text('Credential reference names an optional environment variable. Enter API keys only in the separate masked API key field.'),
          ] else if (widget.section == 'voice') ...[
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
                      }),
            const Text(
                'Audio runs on the coordinator computer. Auto uses native audio on Windows/macOS and existing ALSA commands on Linux. System default keeps the current empty identifier.'),
            _backendField(),
            const SizedBox(height: 12),
            _deviceControls(),
            _deviceField('audio_input_device', 'Microphone', 'input'),
            _deviceField('audio_output_device', 'Speaker', 'output'),
            _detectionStrip(),
            _pathField('wake_model'),
            const Text(
                'A wake model is optional for push-to-talk. Listening starts only when you enable it in Assistant; pretrained wake assets have separate licensing. Detection lists existing files only and does not assess them.'),
          ] else if (widget.section == 'host') ...[
            DropdownButtonFormField<String>(
                initialValue: role,
                decoration: InputDecoration(
                    labelText: 'This computer’s role',
                    errorText: c.fieldErrors['role']),
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
            _detectionStrip(),
            for (final key in _visibleKeys)
              key.endsWith('_provider') ? _field(key) : _pathField(key),
            const Text(
                'Saving paths does not enable voice or configure the other speech component. Detected files are existing files, not tested or supported versions.'),
          ],
          const SizedBox(height: 18),
          ProviderProbePanel(
              key: ValueKey('probe-${widget.section}'),
              backend: c.backend,
              selection: _probeSelection(),
              section: widget.section,
              picker: widget.picker,
              onDevice: (key, value) {
                fields[key]?.text = value;
                _remember();
              }),
          if (!c.dirty && !c.busy && !c.inspectionRequired)
            const Text('No unsaved changes in this section.'),
          Wrap(spacing: 12, children: [
            if (c.dirty && !c.inspectionRequired)
              TextButton(
                  onPressed: c.busy
                      ? null
                      : () async {
                          final review = await c.reviewMerge(widget.section);
                          if (review == null || !context.mounted) return;
                          final accepted = await showDialog<bool>(
                              context: context,
                              builder: (context) => AlertDialog(
                                      title: const Text(
                                          'Review edits against latest saved values'),
                                      content: SizedBox(
                                          width: 600,
                                          child: ConstrainedBox(
                                              constraints: const BoxConstraints(
                                                  maxHeight: 360),
                                              child: SingleChildScrollView(
                                                  child: SelectableText(
                                                      'Saved revision: ${review.revision}\nOnly fields you edited replace the latest saved values.\nConflicting fields that your edits would replace: ${review.conflicts.isEmpty ? 'none' : review.conflicts.join(', ')}.\nThis updates the draft only. You must press Save afterward.\n\n${review.latest.keys.map((key) => '$key\nSaved: ${_reviewValue(key, review.latest[key])}\nYour edit: ${_reviewValue(key, review.edited[key])}').join('\n\n')}')))),
                                      actions: [
                                        TextButton(
                                            onPressed: () =>
                                                Navigator.pop(context, false),
                                            child: const Text('Cancel')),
                                        FilledButton(
                                            onPressed: () =>
                                                Navigator.pop(context, true),
                                            child:
                                                const Text('Use merged draft'))
                                      ]));
                          if (accepted == true) {
                            c.acceptMerge(widget.section, review);
                          }
                        },
                  child: const Text('Review against latest values')),
            FilledButton(
                onPressed: c.busy ||
                        c.inspectionRequired ||
                        c.writerBlocked ||
                        discovery.blockingIn(_sectionAssetKeys) ||
                        !c.dirty
                    ? null
                    : _save,
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
        if (c.root.isEmpty &&
            (c.error.isNotEmpty ||
                c.inspectionRequired ||
                c.writerReview != null)) ...[
          TextButton(
              onPressed: c.busy ? null : c.inspectWriter,
              child: const Text('Inspect interrupted writer')),
          if (c.writerReview?['recoverable'] == true)
            OutlinedButton(
                onPressed: c.busy
                    ? null
                    : () async {
                        final plan = c.writerReview!['plan_id'] as String;
                        final accepted = await showDialog<bool>(
                            context: context,
                            builder: (context) => AlertDialog(
                                    title: const Text(
                                        'Acknowledge inspected save state?'),
                                    content: Text(
                                        'Saved revision: ${c.writerReview!['revision']}\nThis releases only the owned interrupted-writer journal. No save is replayed and no incomplete files are removed.'),
                                    actions: [
                                      TextButton(
                                          onPressed: () =>
                                              Navigator.pop(context, false),
                                          child: const Text('Cancel')),
                                      FilledButton(
                                          onPressed: () =>
                                              Navigator.pop(context, true),
                                          child: const Text(
                                              'Recover inspected writer'))
                                    ]));
                        if (accepted == true) await c.recoverWriter(plan);
                      },
                child: const Text('Review writer recovery')),
        ],
      ]);

  Iterable<String> get _visibleKeys {
    if (widget.section == 'stt') {
      return fields['stt_provider']?.text == 'whisper.cpp'
          ? ['stt_provider', 'whisper_bin', 'whisper_model']
          : ['stt_provider', 'stt_executable', 'stt_model'];
    }
    if (widget.section == 'tts') {
      return fields['tts_provider']?.text == 'kokoro'
          ? ['tts_provider', 'kokoro_model', 'kokoro_voices']
          : ['tts_provider', 'tts_executable', 'tts_model'];
    }
    return _keys;
  }

  String _reviewValue(String key, Object? value) {
    var text = '${value ?? ''}'.replaceAll(RegExp(r'[\x00-\x1f\x7f]'), ' ');
    if (key == 'credential_env' &&
        text.isNotEmpty &&
        !RegExp(r'^[A-Z][A-Z0-9_]{0,127}$').hasMatch(text)) {
      return '(invalid credential reference)';
    }
    return text.length > 200 ? '${text.substring(0, 200)}…' : text;
  }
}
