import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import '../controllers/setup_controller.dart';
import '../../../widgets/glass_surface.dart';
import '../../../app/setup/native_picker.dart';

/// A local coordinator-host configuration wizard, never a remote admin console.
class SetupWizardPage extends StatefulWidget {
  const SetupWizardPage(
      {super.key,
      required this.controller,
      this.initialStep = 0,
      this.picker = const UnavailablePicker()});
  final int initialStep;
  final NativePicker picker;
  final SetupController controller;
  @override
  State<SetupWizardPage> createState() => _SetupWizardPageState();
}

class _SetupWizardPageState extends State<SetupWizardPage> {
  int step = 0;
  late String role, provider, format;
  late bool voice;
  final fields = <String, TextEditingController>{};
  SetupController get c => widget.controller;
  @override
  void initState() {
    super.initState();
    step = widget.initialStep;
    final selection = c.selection;
    final model = selection['model'] as Map?;
    final audio = selection['voice'] as Map?;
    role = selection['role'] as String? ?? 'combined';
    provider = model?['provider'] as String? ?? 'later';
    voice = audio?['enabled'] == true;
    format = c.format;
    for (final name in [
      'name',
      'endpoint',
      'whisper_bin',
      'whisper_model',
      'kokoro_model',
      'kokoro_voices',
      'output'
    ]) {
      final value = switch (name) {
        'name' => model?['name'] ?? '',
        'endpoint' => model?['endpoint'] ?? 'http://127.0.0.1:11434',
        'output' => c.output,
        _ => audio?[name] ?? ''
      };
      fields[name] = TextEditingController(text: value as String);
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      if (c.reviewResult == null && !c.saved) _update();
      if (step == 3 && voice) unawaited(_detectVoice());
    });
  }

  @override
  void dispose() {
    // The application owns the controller and its coordinator. Navigation after
    // a successful handoff must not cancel that coordinator.
    for (final field in fields.values) {
      field.dispose();
    }
    super.dispose();
  }

  void _update() {
    c.update({
      'role': role,
      if (provider != 'later' && role != 'execution')
        'model': {
          'provider': provider,
          'name': fields['name']!.text.trim(),
          if (fields['endpoint']!.text.trim().isNotEmpty)
            'endpoint': fields['endpoint']!.text.trim()
        },
      'voice': {
        if (voice && role != 'execution')
          ...?c.selection['voice'] as Map<String, dynamic>?,
        'enabled': voice && role != 'execution',
        if (voice && role != 'execution')
          for (final key in [
            'whisper_bin',
            'whisper_model',
            'kokoro_model',
            'kokoro_voices'
          ])
            key: fields[key]!.text.trim()
      }
    }, fields['output']!.text, exportFormat: format);
  }

  Widget _field(String name, String label, {String? hint}) => Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: TextField(
          key: ValueKey('setup-$name'),
          controller: fields[name],
          enabled: !c.busy,
          decoration: InputDecoration(
              labelText: label,
              hintText: hint,
              border: const OutlineInputBorder(),
              suffixIcon: [
                'whisper_bin',
                'whisper_model',
                'kokoro_model',
                'kokoro_voices'
              ].contains(name)
                  ? IconButton(
                      tooltip: 'Browse $label',
                      icon: const Icon(Icons.folder_open),
                      onPressed: c.busy
                          ? null
                          : () async {
                              final path = await widget.picker.pickFile();
                              if (!mounted || path == null) return;
                              fields[name]!.text = path;
                              if (c.recoveryRequired) await c.recover();
                              if (mounted) _update();
                            })
                  : null),
          onChanged: (_) async {
            if (c.recoveryRequired) await c.recover();
            if (mounted && !c.recoveryRequired) _update();
          }));
  Widget _note(String value) =>
      Padding(padding: const EdgeInsets.only(bottom: 16), child: Text(value));
  Widget _json(Object value) =>
      ExpansionTile(title: const Text('Advanced details'), children: [
        SelectableText(const JsonEncoder.withIndent('  ').convert(value),
            style: const TextStyle(fontFamily: 'monospace', fontSize: 12))
      ]);

  void _advance() {
    setState(() => step++);
    if (step == 5) unawaited(c.prepare());
    if (step == 3 && voice) unawaited(_detectVoice());
  }

  Future<void> _detectVoice() async {
    await c.detectVoice();
    if (!mounted || c.error.isNotEmpty) return;
    final candidates = c.voiceInventory?['candidates'] as Map? ?? {};
    var changed = false;
    for (final name in [
      'whisper_bin',
      'whisper_model',
      'kokoro_model',
      'kokoro_voices'
    ]) {
      final values = candidates[name] as List? ?? [];
      if (values.length == 1 && fields[name]!.text != values.single) {
        fields[name]!.text = values.single as String;
        changed = true;
      }
    }
    if (changed) _update();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final locked = c.busy || c.recoveryRequired;
        const titles = [
          'Welcome',
          'Environment',
          'Model',
          'Voice',
          'Execution',
          'Review & verify'
        ];
        return SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Center(
                child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 820),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text('Set up this computer',
                              style: Theme.of(context).textTheme.headlineSmall),
                          const SizedBox(height: 8),
                          _note(
                              'LOCAL SETUP · Existing components only. No model downloads, runtime installation, services or permission grants.'),
                          LinearProgressIndicator(value: (step + 1) / 6),
                          const SizedBox(height: 16),
                          Text('${step + 1} of 6 · ${titles[step]}',
                              style: Theme.of(context).textTheme.titleMedium),
                          const SizedBox(height: 18),
                          GlassSurface(
                              child: Padding(
                                  padding: const EdgeInsets.all(20),
                                  child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.stretch,
                                      children: switch (step) {
                                        0 => [
                                            _note(
                                                'The coordinator host owns models, voice, conversations and policy. Execution nodes run authorized tools.'),
                                            DropdownButtonFormField<String>(
                                                isExpanded: true,
                                                initialValue: role,
                                                decoration: const InputDecoration(
                                                    labelText:
                                                        'This machine’s role'),
                                                items: const [
                                                  DropdownMenuItem(
                                                      value: 'combined',
                                                      child: Text(
                                                          'Coordinator host + local execution')),
                                                  DropdownMenuItem(
                                                      value: 'coordinator',
                                                      child: Text(
                                                          'Coordinator host — paired execution node later')),
                                                  DropdownMenuItem(
                                                      value: 'execution',
                                                      child: Text(
                                                          'Execution node only — advanced/headless'))
                                                ],
                                                onChanged: locked
                                                    ? null
                                                    : (value) {
                                                        setState(() {
                                                          role = value!;
                                                          if (role ==
                                                              'execution') {
                                                            provider = 'later';
                                                            voice = false;
                                                          }
                                                        });
                                                        _update();
                                                      }),
                                            const SizedBox(height: 12),
                                            _note(
                                                'A Jetson can be the coordinator and a laptop its execution node. Headless setup remains available for either role.'),
                                          ],
                                        1 => [
                                            _note(
                                                'These diagnostics inspect this computer only. No remote host is installed or reconfigured.'),
                                            _note(
                                                'The local helper needs Python with Odessy installed. Developers can select an interpreter/source root with ODESSY_SETUP_PYTHON and ODESSY_SETUP_ROOT build defines.'),
                                            OutlinedButton(
                                                onPressed:
                                                    locked ? null : c.verify,
                                                child: const Text(
                                                    'Check existing components')),
                                            if (c.diagnostics != null)
                                              _json(c.diagnostics!),
                                          ],
                                        2 => role == 'execution'
                                            ? [
                                                _note(
                                                    'Execution nodes need no LLM. Model configuration is skipped.')
                                              ]
                                            : [
                                                _note(
                                                    'Choose an existing model or configure later. LFM is never downloaded automatically. Inventory does not prove tool compatibility.'),
                                                DropdownButtonFormField<String>(
                                                    isExpanded: true,
                                                    initialValue: provider,
                                                    decoration:
                                                        const InputDecoration(
                                                            labelText:
                                                                'Model provider'),
                                                    items: [
                                                      for (final value in [
                                                        'later',
                                                        'ollama',
                                                        'lfm',
                                                        'claude',
                                                        'openai',
                                                        'deepseek',
                                                        'qwen',
                                                        'kimi',
                                                        'custom'
                                                      ])
                                                        DropdownMenuItem(
                                                            value: value,
                                                            child: Text(value ==
                                                                    'later'
                                                                ? 'Configure later'
                                                                : value))
                                                    ],
                                                    onChanged: locked
                                                        ? null
                                                        : (value) {
                                                            setState(() {
                                                              provider = value!;
                                                              fields['endpoint']!
                                                                  .text = [
                                                                'ollama',
                                                                'lfm'
                                                              ].contains(
                                                                      provider)
                                                                  ? 'http://127.0.0.1:11434'
                                                                  : '';
                                                            });
                                                            _update();
                                                          }),
                                                const SizedBox(height: 16),
                                                if (provider != 'later') ...[
                                                  _field('endpoint',
                                                      'Provider endpoint (optional for preset cloud providers)'),
                                                  if (['ollama', 'lfm']
                                                      .contains(provider))
                                                    OutlinedButton(
                                                        onPressed: locked
                                                            ? null
                                                            : () => c.discover(
                                                                fields['endpoint']!
                                                                    .text),
                                                        child: const Text(
                                                            'Detect installed models')),
                                                  if (c.inventory != null) ...[
                                                    Text(
                                                        'Discovery: ${c.inventory!['status']}'),
                                                    for (final model
                                                        in (c.inventory![
                                                                    'models']
                                                                as List? ??
                                                            []))
                                                      TextButton(
                                                          onPressed: locked
                                                              ? null
                                                              : () {
                                                                  fields['name']!
                                                                      .text = model[
                                                                          'name']
                                                                      as String;
                                                                  _update();
                                                                },
                                                          child: Text(
                                                              model['name']
                                                                  as String)),
                                                  ],
                                                  _field('name',
                                                      'Existing model name'),
                                                  if (!['ollama', 'lfm']
                                                      .contains(provider))
                                                    _note(
                                                        'Supply credentials through the provider environment. This wizard never stores or requests the key.'),
                                                ],
                                              ],
                                        3 => role == 'execution'
                                            ? [
                                                _note(
                                                    'Execution nodes do not need speech models or microphone access.')
                                              ]
                                            : [
                                                SwitchListTile(
                                                    contentPadding:
                                                        EdgeInsets.zero,
                                                    title: const Text(
                                                        'Enable existing voice components'),
                                                    value: voice,
                                                    onChanged: locked
                                                        ? null
                                                        : (value) {
                                                            setState(() =>
                                                                voice = value);
                                                            _update();
                                                            if (voice) {
                                                              unawaited(
                                                                  _detectVoice());
                                                            }
                                                          }),
                                                _note(
                                                    'Whisper.cpp and Kokoro are supported defaults. Missing assets remain unavailable; no download or microphone recording starts here.'),
                                                if (voice) ...[
                                                  OutlinedButton.icon(
                                                      onPressed: locked
                                                          ? null
                                                          : _detectVoice,
                                                      icon: const Icon(
                                                          Icons.search),
                                                      label: const Text(
                                                          'Find existing voice files')),
                                                  if (c.voiceInventory != null)
                                                    Text((c.voiceInventory![
                                                                        'missing']
                                                                    as List? ??
                                                                [])
                                                            .isEmpty
                                                        ? 'Existing voice files found. Review the selected paths.'
                                                        : 'Some files were not found. Browse to select them; nothing was downloaded.'),
                                                  _field('whisper_bin',
                                                      'Whisper executable'),
                                                  _field('whisper_model',
                                                      'Whisper model file'),
                                                  _field('kokoro_model',
                                                      'Kokoro ONNX model'),
                                                  _field('kokoro_voices',
                                                      'Kokoro voices file'),
                                                  _note(
                                                      'Model and phonemizer licenses remain applicable. Wake-word assets are configured separately.'),
                                                ],
                                              ],
                                        4 => [
                                            _note(role == 'combined'
                                                ? 'Local execution uses existing policy and workspace controls. This wizard does not grant workspaces.'
                                                : role == 'execution'
                                                    ? 'This node must locally enroll and grant its coordinator access. Installing a node does not authorize commands.'
                                                    : 'Add the laptop from the coordinator host. The laptop operator must verify enrollment and grant a workspace locally.'),
                                            _note(
                                                'Pairing setup, grants, service installation and automatic remote deployment are not implemented in this wizard. Existing odessy-link CLI remains available.'),
                                          ],
                                        _ => [
                                            Text(provider == 'later'
                                                ? 'Model: configure later'
                                                : 'Model: ${fields['name']!.text} ($provider)'),
                                            const SizedBox(height: 12),
                                            _note(voice
                                                ? 'Voice: existing components selected'
                                                : 'Voice: off'),
                                            _field(
                                                'output', 'Configuration file',
                                                hint:
                                                    'Full path to a new configuration file'),
                                            DropdownButtonFormField<String>(
                                                isExpanded: true,
                                                initialValue: format,
                                                decoration: const InputDecoration(
                                                    labelText:
                                                        'Configuration format'),
                                                items: const [
                                                  DropdownMenuItem(
                                                      value: 'selection',
                                                      child: Text(
                                                          'Setup configuration')),
                                                  DropdownMenuItem(
                                                      value: 'env',
                                                      child: Text(
                                                          'Runtime environment')),
                                                ],
                                                onChanged: locked
                                                    ? null
                                                    : (value) {
                                                        setState(() =>
                                                            format = value!);
                                                        _update();
                                                      }),
                                            const SizedBox(height: 12),
                                            _note(
                                                'Save and connect checks your choices, saves the reviewed configuration, then opens a real conversation. Nothing is downloaded. Existing files are preserved.'),
                                            if (c.diagnostics?['voice']
                                                    is Map &&
                                                (c.diagnostics!['voice']
                                                        as Map)['status'] ==
                                                    'missing')
                                              Text(
                                                  'Voice needs attention: ${((c.diagnostics!['voice'] as Map)['missing'] as List).join(', ')}. You can fix the paths or disable voice to use text.'),
                                            if (c.reviewResult != null ||
                                                c.diagnostics != null)
                                              _json({
                                                'review': c.reviewResult,
                                                'diagnostics': c.diagnostics
                                              }),
                                          ],
                                      }))),
                          const SizedBox(height: 16),
                          Text(c.status, key: const ValueKey('setup-status')),
                          if (c.error.isNotEmpty)
                            Padding(
                                padding: const EdgeInsets.only(top: 8),
                                child: Text(c.error,
                                    style: TextStyle(
                                        color: Theme.of(context)
                                            .colorScheme
                                            .error))),
                          if (c.busy) ...[
                            const SizedBox(height: 12),
                            const LinearProgressIndicator(),
                            TextButton(
                                onPressed: c.cancel,
                                child: const Text('Cancel operation'))
                          ],
                          if (c.output.isNotEmpty)
                            TextButton(
                                onPressed: c.busy ? null : c.recover,
                                child: const Text(
                                    'Inspect saved state — do not replay')),
                          const SizedBox(height: 18),
                          Row(children: [
                            OutlinedButton(
                                onPressed: step == 0 || c.busy
                                    ? null
                                    : () => setState(() => step--),
                                child: const Text('Back')),
                            const Spacer(),
                            FilledButton(
                                onPressed: c.busy || c.completing
                                    ? null
                                    : step == 5
                                        ? c.finish
                                        : _advance,
                                child: Text(step == 5
                                    ? 'Save and connect'
                                    : 'Continue')),
                          ]),
                        ]))));
      });
}
