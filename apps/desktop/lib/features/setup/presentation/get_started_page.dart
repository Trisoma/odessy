import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

import '../../../app/setup/native_picker.dart';
import '../../../app/theme.dart';
import '../controllers/installed_manage_controller.dart';
import 'path_field.dart';
import 'setup_shell.dart';
import '../controllers/model_catalog_controller.dart';
import 'model_picker.dart';

/// First-run configuration: installing files is done; this is getting ready to chat.
///
/// Model choice is explicit and reversible (On this computer / Remote or cloud /
/// Configure later). Nothing contacts a provider until the user asks, nothing is
/// downloaded, and no credential is entered or stored here.
class GetStartedPage extends StatefulWidget {
  const GetStartedPage({
    super.key,
    required this.controller,
    required this.onStart,
    this.onClose,
    this.picker = const UnavailablePicker(),
    this.coordinatorReady = false,
    this.initialVoice = false,
  });

  final InstalledManageController controller;
  final VoidCallback onStart;
  final VoidCallback? onClose;
  final NativePicker picker;
  final bool coordinatorReady;
  final bool initialVoice;

  @override
  State<GetStartedPage> createState() => _GetStartedPageState();
}

class _GetStartedPageState extends State<GetStartedPage> {
  final endpoint = TextEditingController(text: 'http://127.0.0.1:11434');
  final model = TextEditingController();
  final workspace = TextEditingController();
  final credential = TextEditingController();
  final localCredential = TextEditingController();
  String choice = 'local', preset = 'openai', localProvider = 'ollama';
  String _savedChoice = 'local';
  String _localEndpoint = 'http://127.0.0.1:11434', _remoteEndpoint = '';
  bool initialised = false;
  late final ModelCatalogController catalog;
  final _modelChoiceKey = GlobalKey();
  final _voiceKey = GlobalKey();
  bool showVoice = false, voiceEnabled = false;
  final voicePaths = <String, TextEditingController>{
    for (final name in [
      'whisper_bin',
      'whisper_model',
      'kokoro_model',
      'kokoro_voices',
    ])
      name: TextEditingController(),
  };

  void _chooseModel() {
    _selectChoice(
      choice == 'local' || choice == 'remote' ? choice : _savedChoice,
    );
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final target = _modelChoiceKey.currentContext;
      if (mounted && target != null) Scrollable.ensureVisible(target);
    });
  }

  void _selectChoice(String value) {
    setState(() {
      if (choice == 'local') _localEndpoint = endpoint.text;
      if (choice == 'remote') _remoteEndpoint = endpoint.text;
      choice = value;
      if (value == 'local' || value == 'remote') _savedChoice = value;
      if (value == 'local') endpoint.text = _localEndpoint;
      if (value == 'remote') endpoint.text = _remoteEndpoint;
    });
  }

  void _voiceHelp() {
    setState(() => showVoice = true);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final target = _voiceKey.currentContext;
      if (mounted && target != null) Scrollable.ensureVisible(target);
    });
  }

  InstalledManageController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    catalog = ModelCatalogController(c.backend);
    showVoice = widget.initialVoice;
    c.addListener(_refresh);
    if (c.status.isNotEmpty) _fromStatus();
  }

  void _fromStatus() {
    if (initialised) return;
    initialised = true;
    workspace.text = c.workspace;
    final selection = c.spec['selection'];
    final savedModel = selection is Map ? selection['model'] : null;
    if (savedModel is Map) {
      final provider = savedModel['provider'];
      model.text =
          savedModel['name'] is String ? savedModel['name'] as String : '';
      final savedEndpoint = savedModel['endpoint'] is String
          ? savedModel['endpoint'] as String
          : '';
      final savedCredential = savedModel['credential_env'] is String
          ? savedModel['credential_env'] as String
          : '';
      if (provider == 'ollama' || provider == 'lfm') {
        localProvider = provider as String;
        _localEndpoint =
            savedEndpoint.isEmpty ? 'http://127.0.0.1:11434' : savedEndpoint;
        localCredential.text = savedCredential;
        _savedChoice = 'local';
      } else if (const {
        'openai',
        'claude',
        'deepseek',
        'qwen',
        'kimi',
        'custom',
      }.contains(provider)) {
        preset = provider as String;
        _remoteEndpoint = savedEndpoint;
        credential.text = savedCredential;
        _savedChoice = 'remote';
      }
    }
    final audio = selection is Map ? selection['voice'] : null;
    if (audio is Map) {
      voiceEnabled = audio['enabled'] == true;
      for (final entry in voicePaths.entries) {
        entry.value.text = '${audio[entry.key] ?? ''}';
      }
    }
    if (c.modelConfigured) choice = 'done';
  }

  void _refresh() {
    if (!mounted) return;
    _fromStatus();
    setState(() {});
  }

  @override
  void dispose() {
    c.removeListener(_refresh);
    catalog.dispose();
    endpoint.dispose();
    model.dispose();
    workspace.dispose();
    credential.dispose();
    localCredential.dispose();
    for (final controller in voicePaths.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Json get _selection => {
        'role': c.role,
        if (c.spec['selection'] is Map &&
            (c.spec['selection'] as Map)['voice'] is Map)
          'voice': (c.spec['selection'] as Map)['voice'],
        if (choice == 'local')
          'model': {
            'provider': localProvider,
            'name': model.text.trim(),
            'endpoint': endpoint.text.trim(),
            if (localCredential.text.trim().isNotEmpty)
              'credential_env': localCredential.text.trim(),
          },
        if (choice == 'remote')
          'model': {
            'provider': preset,
            'name': model.text.trim(),
            if (endpoint.text.trim().isNotEmpty)
              'endpoint': endpoint.text.trim(),
            if (credential.text.trim().isNotEmpty)
              'credential_env': credential.text.trim(),
          },
      };

  bool get _valid => switch (choice) {
        'local' => model.text.trim().isNotEmpty &&
            endpoint.text.trim().isNotEmpty &&
            _validCredential(localCredential.text),
        'remote' => model.text.trim().isNotEmpty &&
            (preset != 'custom' || endpoint.text.trim().isNotEmpty) &&
            _validCredential(credential.text),
        _ => true,
      };

  bool _validCredential(String value) =>
      value.trim().isEmpty ||
      RegExp(r'^[A-Z][A-Z0-9_]{0,127}$').hasMatch(value.trim());

  Future<void> _browseWorkspace() async {
    final selected = await widget.picker.pickDirectory(
      initial: workspace.text.isEmpty ? null : workspace.text,
    );
    if (selected == null || selected == workspace.text) return;
    setState(() => workspace.text = selected);
  }

  Future<void> _save() async {
    if (!_valid || c.busy) return;
    if (choice == 'later') {
      _chooseModel();
      return;
    }
    await c.reviewConfiguration(_selection, workspace.text.trim());
    if (!mounted) return;
    if (c.reviewedAction == 'package-configure-review' && c.canConfirm) {
      await c.confirm();
      if (mounted &&
          c.error.isEmpty &&
          c.result['configured'] == true &&
          c.notice.isNotEmpty) {
        widget.onStart();
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final configured = c.modelConfigured;
    final readyToStart = configured && choice == 'done';
    return SetupShell(
      title: 'Get started with Odessy',
      subtitle: configured
          ? 'Review your setup and start chatting.'
          : 'Odessy is installed. Choose how it should reach a model.',
      footer: SetupFooter(
        primaryLabel: choice == 'later'
            ? 'Configure a model'
            : readyToStart
                ? 'Start chatting'
                : 'Save and continue',
        onPrimary: c.busy
            ? null
            : choice == 'later'
                ? _chooseModel
                : readyToStart
                    ? widget.onStart
                    : (_valid ? _save : null),
        secondaryLabel: 'Configure later',
        onSecondary: c.busy ? null : () => _selectChoice('later'),
        busy: c.busy,
        note: readyToStart
            ? 'Start chatting is available because a model is configured and the local coordinator is ready.'
            : configured
                ? 'Changes apply on the next launch; the current session keeps its configuration.'
                : 'No model is downloaded and no provider is contacted until you ask.',
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (c.error.isNotEmpty)
            SetupMessage(
              error: true,
              text: c.error,
              actionLabel: 'Retry',
              onAction: c.busy ? null : c.refreshStatus,
            ),
          if (c.notice.isNotEmpty) SetupMessage(text: c.notice),
          if (choice == 'done')
            _configured(context, c)
          else ...[
            SetupSection(
              title: 'Where should the model run?',
              description:
                  'Choose an existing local model, a compatible remote endpoint, or finish later. Nothing is pulled automatically.',
              child: SegmentedButton<String>(
                key: _modelChoiceKey,
                segments: const [
                  ButtonSegment(
                    value: 'local',
                    icon: Icon(Icons.computer_outlined, size: 18),
                    label: Text('On this computer'),
                  ),
                  ButtonSegment(
                    value: 'remote',
                    icon: Icon(Icons.cloud_outlined, size: 18),
                    label: Text('Remote or cloud'),
                  ),
                  ButtonSegment(
                    value: 'later',
                    icon: Icon(Icons.schedule_outlined, size: 18),
                    label: Text('Configure later'),
                  ),
                ],
                selected: {choice},
                onSelectionChanged:
                    c.busy ? null : (value) => _selectChoice(value.first),
              ),
            ),
            if (choice == 'local') _local(context),
            if (choice == 'remote') _remote(context),
            if (choice == 'later')
              const SetupMessage(
                text:
                    'Model setup is deferred. Odessy stays open, and no model or service is started. Choose Configure a model whenever you are ready, use Maintenance and repair, or use Close to exit.',
              ),
            SetupSection(
              title: 'Workspace',
              description:
                  'Default: Documents/Odessy on Windows, or ~/Odessy on Linux/macOS. Use Browse to choose another project folder. It stays separate from installed application files.',
              child: PathField(
                fieldKey: const Key('get-started-workspace'),
                controller: workspace,
                label: 'Workspace folder',
                hint: 'Choose an existing folder',
                enabled: !c.busy,
                onBrowse: _browseWorkspace,
              ),
            ),
            _checklist(context, c),
            if (showVoice) _voiceConfiguration(),
          ],
        ],
      ),
    );
  }

  Widget _local(BuildContext context) => SetupSection(
        title: 'On this computer',
        description:
            'Odessy reads the model list from the endpoint you name. It does not install or download anything.',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            DropdownButtonFormField<String>(
              initialValue: localProvider,
              decoration: const InputDecoration(labelText: 'Local backend'),
              items: const [
                DropdownMenuItem(value: 'ollama', child: Text('Ollama')),
                DropdownMenuItem(value: 'lfm', child: Text('LFM')),
              ],
              onChanged: c.busy
                  ? null
                  : (value) => setState(() => localProvider = value!),
            ),
            Row(
              children: [
                Expanded(
                  child: PathField(
                    fieldKey: const Key('get-started-endpoint'),
                    controller: endpoint,
                    label: 'Ollama endpoint',
                    hint: 'http://127.0.0.1:11434',
                    enabled: !c.busy,
                    onChanged: (_) => setState(() {}),
                  ),
                ),
              ],
            ),
            OutlinedButton.icon(
              key: const Key('get-started-discover'),
              onPressed: c.busy || c.discovering
                  ? null
                  : () => c.discover(endpoint: endpoint.text.trim()),
              icon: const Icon(Icons.search, size: 18),
              label: Text(
                c.discovering
                    ? 'Reading the endpoint…'
                    : 'Find installed models',
              ),
            ),
            const SizedBox(height: OdessyTokens.spaceMedium),
            _models(context),
            const SizedBox(height: OdessyTokens.spaceMedium),
            TextField(
              controller: localCredential,
              enabled: !c.busy,
              onChanged: (_) => setState(() {}),
              decoration: const InputDecoration(
                labelText: 'Local credential environment variable (optional)',
                helperText: 'Variable name only; no token is stored here.',
              ),
            ),
          ],
        ),
      );

  Widget _models(BuildContext context) {
    final list = c.discovery['models'];
    final names = <String>[
      if (list is List)
        for (final entry in list)
          if (entry is Map && entry['name'] is String) entry['name'] as String,
    ];
    if (names.isNotEmpty) {
      return DropdownButtonFormField<String>(
        key: const Key('get-started-model-list'),
        initialValue:
            names.contains(model.text.trim()) ? model.text.trim() : null,
        decoration: const InputDecoration(labelText: 'Installed model'),
        items: [
          for (final name in names)
            DropdownMenuItem(value: name, child: Text(name)),
        ],
        onChanged:
            c.busy ? null : (value) => setState(() => model.text = value ?? ''),
      );
    }
    return TextField(
      key: const Key('get-started-model'),
      controller: model,
      enabled: !c.busy,
      onChanged: (_) => setState(() {}),
      decoration: InputDecoration(
        labelText: 'Model name',
        hintText: 'For example llama3.1:8b',
        helperText: c.discovery['status'] == 'empty'
            ? 'The endpoint answered but has no models. Odessy will not download one.'
            : c.discovery['status'] == 'unreachable'
                ? 'Ollama is not answering at this address. Start it, or choose a remote endpoint.'
                : 'Type the exact name of a model you already have.',
      ),
    );
  }

  Widget _remote(BuildContext context) => SetupSection(
        title: 'Remote or cloud',
        description:
            'Choose a supported provider preset or a compatible endpoint, then name the model. Authentication is referenced by environment-variable name only; no key is entered here.',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            DropdownButtonFormField<String>(
              initialValue: preset,
              decoration: const InputDecoration(labelText: 'Provider'),
              items: const [
                DropdownMenuItem(
                    value: 'openai', child: Text('OpenAI-compatible')),
                DropdownMenuItem(value: 'claude', child: Text('Claude')),
                DropdownMenuItem(value: 'deepseek', child: Text('DeepSeek')),
                DropdownMenuItem(value: 'qwen', child: Text('Qwen')),
                DropdownMenuItem(value: 'kimi', child: Text('Kimi')),
                DropdownMenuItem(
                    value: 'custom', child: Text('Custom endpoint')),
              ],
              onChanged: c.busy
                  ? null
                  : (value) => setState(() {
                        preset = value ?? 'openai';
                        model.clear();
                      }),
            ),
            const SizedBox(height: OdessyTokens.spaceMedium),
            ModelPicker(
              catalog: catalog,
              provider: preset,
              value: model,
              endpoint: endpoint.text.trim(),
              credential: credential.text.trim(),
              enabled: !c.busy,
              fieldKey: const Key('get-started-remote-model'),
              onChanged: () => setState(() {}),
            ),
            const SizedBox(height: OdessyTokens.spaceMedium),
            TextField(
              key: const Key('get-started-remote-endpoint'),
              controller: endpoint,
              onChanged: (_) => setState(() {}),
              enabled: !c.busy,
              decoration: const InputDecoration(
                labelText: 'Endpoint (optional for presets)',
                hintText: 'https://…',
              ),
            ),
            const SizedBox(height: OdessyTokens.spaceMedium),
            TextField(
              key: const Key('get-started-credential'),
              controller: credential,
              onChanged: (_) => setState(() {}),
              enabled: !c.busy,
              decoration: const InputDecoration(
                labelText: 'Credential environment variable (optional)',
                hintText: 'ODESSY_CLOUD_API_KEY',
                helperText:
                    'The variable name only. Keys stay in the environment; they are never stored in settings.',
              ),
            ),
          ],
        ),
      );

  Widget _configured(BuildContext context, InstalledManageController c) =>
      SetupSection(
        title: 'Ready',
        description: 'This installation is configured.',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'The local coordinator starts when you continue. If it cannot start, setup stays open and shows the reason.',
            ),
            const SizedBox(height: OdessyTokens.spaceMedium),
            OutlinedButton(
              onPressed: c.busy ? null : _chooseModel,
              child: const Text('Change model'),
            ),
            OutlinedButton(
              onPressed: c.busy ? null : c.refreshStatus,
              child: const Text('Re-read installation state'),
            ),
          ],
        ),
      );

  Widget _checklist(BuildContext context, InstalledManageController c) {
    final checked = <(String, bool, String, String, VoidCallback)>[
      (
        'Model configured',
        c.modelConfigured,
        c.modelConfigured ? 'Configured' : 'Choose a model',
        'model',
        _chooseModel,
      ),
      (
        'Endpoint reachable (when tested)',
        c.discovery['status'] == 'ok',
        c.discovery['status'] == 'ok'
            ? 'Answered'
            : 'Choose/test local endpoint',
        'endpoint',
        () {
          _chooseModel();
          if (choice == 'local') c.discover(endpoint: endpoint.text.trim());
        },
      ),
      (
        'Workspace selected',
        workspace.text.trim().isNotEmpty,
        workspace.text.trim().isEmpty
            ? 'Choose a folder'
            : workspace.text.trim(),
        'workspace',
        () => _browseWorkspace(),
      ),
      (
        'Voice',
        voiceEnabled,
        'Configure existing voice assets',
        'voice',
        _voiceHelp,
      ),
    ];
    return SetupSection(
      title: 'Setup checklist',
      description:
          'This is configuration state, not a promise that a model reply will succeed.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final item in checked)
            ListTile(
              key: Key('checklist-${item.$4}'),
              contentPadding: EdgeInsets.zero,
              onTap: c.busy ? null : item.$5,
              leading: Icon(
                item.$2 ? Icons.check_circle : Icons.radio_button_unchecked,
                size: 16,
                color: item.$2 ? OdessyTokens.accent : Colors.grey,
              ),
              title: Text(item.$1),
              subtitle: Tooltip(
                message: item.$3,
                child: Text(
                  item.$3,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              trailing: const Icon(Icons.chevron_right),
            ),
        ],
      ),
    );
  }

  Widget _voiceConfiguration() => Container(
        key: _voiceKey,
        child: SetupSection(
          title: 'Voice configuration',
          description:
              'Select existing Whisper and Kokoro files. Saving validates configuration; it does not download assets or start microphone capture.',
          child: Column(
            children: [
              SwitchListTile(
                title: const Text('Enable existing voice assets'),
                value: voiceEnabled,
                onChanged: c.busy
                    ? null
                    : (value) => setState(() => voiceEnabled = value),
              ),
              for (final entry in voicePaths.entries)
                PathField(
                  controller: entry.value,
                  label: entry.key.replaceAll('_', ' '),
                  enabled: !c.busy,
                  onBrowse: () async {
                    final selected = await widget.picker.pickFile(
                      initial: entry.value.text,
                    );
                    if (mounted && selected != null) {
                      setState(() => entry.value.text = selected);
                    }
                  },
                ),
              FilledButton(
                onPressed: c.busy
                    ? null
                    : () async {
                        final existing = c.spec['selection'];
                        await c.reviewConfiguration({
                          ...?existing is Json ? existing : null,
                          'role': c.role,
                          'voice': {
                            'enabled': voiceEnabled,
                            for (final entry in voicePaths.entries)
                              entry.key: entry.value.text.trim(),
                          },
                        }, workspace.text.trim());
                        if (c.canConfirm &&
                            c.reviewedAction == 'package-configure-review') {
                          await c.confirm();
                        }
                      },
                child: const Text('Save voice configuration'),
              ),
            ],
          ),
        ),
      );
}
