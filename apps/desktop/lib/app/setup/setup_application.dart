import 'dart:convert';
import 'dart:ui' show AppExitResponse;

import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

import '../../features/setup/controllers/installed_manage_controller.dart';
import '../../features/setup/controllers/package_setup_controller.dart';
import '../../features/setup/presentation/manage_page.dart';
import '../theme.dart';
import 'installed_startup.dart' show exitApp;
import 'local_setup_backend.dart';

String? installationMode(List<String> args) => args.contains('--installed')
    ? 'installed'
    : args.contains('--setup')
        ? 'setup'
        : args.contains('--manage')
            ? 'manage'
            : null;

typedef SetupBackendFactory = SetupBackend Function();

class SetupApplication extends StatefulWidget {
  const SetupApplication({
    super.key,
    required this.mode,
    this.payloadRoot = '',
    this.installRoot = '',
    this.backendFactory,
    this.onExit,
  });

  final String mode, payloadRoot, installRoot;
  final SetupBackendFactory? backendFactory;
  final Future<void> Function()? onExit;

  @override
  State<SetupApplication> createState() => _SetupApplicationState();
}

class _SetupApplicationState extends State<SetupApplication> {
  late final PackageSetupController controller;
  final location = TextEditingController();
  final model = TextEditingController();
  final endpoint = TextEditingController(text: 'http://127.0.0.1:11434');
  final credential = TextEditingController();
  final whisperBin = TextEditingController();
  final whisperModel = TextEditingController();
  final kokoroModel = TextEditingController();
  final kokoroVoices = TextEditingController();

  int step = 0;
  String role = 'combined', provider = 'later';
  bool voice = false;
  InstalledManageController? management;
  late final AppLifecycleListener lifecycle;
  bool _closing = false;

  PackageSetupController get c => controller;

  SetupBackend _newBackend() =>
      (widget.backendFactory ?? () => LocalSetupBackend(payloadRoot: widget.payloadRoot))();

  @override
  void initState() {
    super.initState();
    controller = PackageSetupController(_newBackend(), progressBackend: _newBackend());
    controller.addListener(_refresh);
    lifecycle = AppLifecycleListener(onExitRequested: () async {
      if (c.busy) {
        final keep = await showDialog<bool>(
            context: context,
            builder: (ctx) => AlertDialog(
                    title: const Text('Cancel and close setup?'),
                    content: const Text(
                        'Reopen setup to inspect interrupted work. Nothing is automatically repeated.'),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(ctx, true),
                          child: const Text('Keep open')),
                      TextButton(
                          onPressed: () => Navigator.pop(ctx, false),
                          child: const Text('Cancel and close')),
                    ]));
        if (keep != false) return AppExitResponse.cancel;
      }
      await _shutdown();
      return AppExitResponse.exit;
    });
    _load();
  }

  void _refresh() {
    if (mounted) setState(() {});
  }

  Future<void> _load() async {
    c.root = widget.installRoot;
    await c.run('context');
    if (!mounted) return;
    location.text = c.root;
    setState(() {});
    if (widget.mode == 'manage' && management == null) _openManagement();
  }

  Future<void> _shutdown() async {
    if (_closing) return;
    _closing = true;
    await c.close();
    management?.dispose();
  }

  Future<void> _closeApplication() async {
    final navigator = Navigator.of(context, rootNavigator: true);
    await _shutdown();
    final exit = widget.onExit;
    if (exit != null) {
      await exit();
      return;
    }
    // The setup window owns no coordinator; closing it ends this process.
    navigator.maybePop();
    exitApp();
  }

  Future<void> _checkLocation() async {
    if (c.busy) return;
    c.edit(destination: location.text);
    await c.run('context');
    if (!mounted) return;
    location.text = c.root;
    setState(() {});
  }

  bool get _voiceComplete =>
      whisperBin.text.trim().isNotEmpty &&
      whisperModel.text.trim().isNotEmpty &&
      kokoroModel.text.trim().isNotEmpty &&
      kokoroVoices.text.trim().isNotEmpty;

  bool get _voiceInvalid => voice && !_voiceComplete;

  bool get _modelIncomplete =>
      role != 'execution' &&
      provider != 'later' &&
      (model.text.trim().isEmpty || endpoint.text.trim().isEmpty);

  Json _selection() => {
        'role': role,
        if (role != 'execution' && provider != 'later')
          'model': {
            'provider': provider,
            'name': model.text.trim(),
            if (endpoint.text.trim().isNotEmpty) 'endpoint': endpoint.text.trim(),
            if (credential.text.trim().isNotEmpty)
              'credential_env': credential.text.trim(),
          },
        if (role != 'execution')
          'voice': {
            'enabled': voice && _voiceComplete,
            if (voice && _voiceComplete) ...{
              'whisper_bin': whisperBin.text.trim(),
              'whisper_model': whisperModel.text.trim(),
              'kokoro_model': kokoroModel.text.trim(),
              'kokoro_voices': kokoroVoices.text.trim(),
            },
          },
      };

  void _edit() => c.edit(destination: location.text, value: _selection());

  void _openManagement() {
    final controller = InstalledManageController(c.backend, spec: {'root': c.root});
    setState(() => management = controller);
    controller.load();
  }

  @override
  void dispose() {
    lifecycle.dispose();
    controller.removeListener(_refresh);
    controller.dispose();
    management?.dispose();
    for (final editor in [
      location,
      model,
      endpoint,
      credential,
      whisperBin,
      whisperModel,
      kokoroModel,
      kokoroVoices,
    ]) {
      editor.dispose();
    }
    super.dispose();
  }

  Widget _field(TextEditingController editor, String label, {String? helper}) =>
      Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: TextField(
              controller: editor,
              enabled: !c.busy,
              onChanged: (_) => _edit(),
              decoration: InputDecoration(labelText: label, helperText: helper)));

  Widget _welcome() => ListView(children: const [
        Text('Install Odessy on this computer'),
        SizedBox(height: 14),
        Text(
            'Odessy is a local assistant owned by you. Setup verifies the downloaded package, '
            'installs it into a folder you choose, and creates a Start-menu entry and an '
            'Installed-apps record.'),
        SizedBox(height: 10),
        Text(
            'Nothing is downloaded from the internet, no model is pulled and no inference runs during setup. '
            'A model you already have can be selected in the next steps, or you can configure one later.'),
        SizedBox(height: 10),
        Text(
            'Administrator rights are not requested. The installation is for this Windows account only.'),
      ]);

  Widget _machine() => ListView(children: [
        DropdownButtonFormField<String>(
            initialValue: role,
            decoration: const InputDecoration(labelText: 'This computer is'),
            items: const [
              DropdownMenuItem(
                  value: 'combined',
                  child: Text('A coordinator that also runs tools locally')),
              DropdownMenuItem(
                  value: 'coordinator',
                  child: Text('A coordinator only (execution happens elsewhere)')),
              DropdownMenuItem(
                  value: 'execution',
                  child: Text('An execution node owned by another coordinator')),
            ],
            onChanged: c.busy
                ? null
                : (value) {
                    setState(() => role = value ?? 'combined');
                    _edit();
                  }),
        const SizedBox(height: 16),
        _field(location, 'Installation folder',
            helper:
                'Use a dedicated folder. Only files recorded by Odessy are removed later.'),
        Align(
            alignment: Alignment.centerLeft,
            child: OutlinedButton.icon(
                onPressed: c.busy ? null : _checkLocation,
                icon: const Icon(Icons.folder_open, size: 18),
                label: const Text('Check this folder'))),
        if (c.existingInstallation)
          Card(
              child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Odessy is already installed here',
                            style: Theme.of(context).textTheme.titleMedium),
                        const SizedBox(height: 6),
                        const Text(
                            'Use management to repair, update, roll back, reconfigure or uninstall the existing installation. Installing into the same folder is refused.'),
                        const SizedBox(height: 10),
                        FilledButton.tonal(
                            onPressed: c.busy ? null : _openManagement,
                            child: const Text('Manage this installation')),
                      ]))),
      ]);

  Widget _modelAndVoice() {
    final models = <String>[];
    final discoveryModels = c.discovery['models'];
    if (discoveryModels is List) {
      for (final entry in discoveryModels) {
        if (entry is Map && entry['name'] is String) {
          models.add(entry['name'] as String);
        }
      }
    }
    return ListView(children: [
      if (role == 'execution')
        const Text(
            'Execution nodes run tools for another coordinator. They need no model, voice or desktop runtime.')
      else ...[
        DropdownButtonFormField<String>(
            initialValue: provider,
            decoration: const InputDecoration(labelText: 'Model'),
            items: const [
              DropdownMenuItem(
                  value: 'later', child: Text('Configure a model later')),
              DropdownMenuItem(
                  value: 'ollama', child: Text('An Ollama model already installed')),
              DropdownMenuItem(
                  value: 'openai',
                  child: Text('A compatible remote endpoint')),
            ],
            onChanged: c.busy
                ? null
                : (value) {
                    setState(() => provider = value ?? 'later');
                    _edit();
                  }),
        if (provider != 'later') ...[
          const SizedBox(height: 12),
          _field(endpoint, 'Endpoint'),
          if (provider == 'ollama')
            Align(
                alignment: Alignment.centerLeft,
                child: OutlinedButton.icon(
                    onPressed: c.busy || c.discovering
                        ? null
                        : () => c.discover(endpoint: endpoint.text.trim()),
                    icon: const Icon(Icons.search, size: 18),
                    label: Text(c.discovering
                        ? 'Reading the endpoint…'
                        : 'Find installed models'))),
          const SizedBox(height: 12),
          if (models.isNotEmpty)
            DropdownButtonFormField<String>(
                initialValue:
                    models.contains(model.text.trim()) ? model.text.trim() : null,
                decoration: const InputDecoration(
                    labelText: 'Installed model'),
                items: [
                  for (final name in models)
                    DropdownMenuItem(value: name, child: Text(name)),
                ],
                onChanged: c.busy
                    ? null
                    : (value) {
                        model.text = value ?? '';
                        _edit();
                      })
          else
            _field(model, 'Existing model name'),
          if (provider != 'ollama')
            _field(credential, 'Credential environment variable (optional)',
                helper:
                    'The name only, for example ODESSY_CLOUD_API_KEY. Never enter a key here.'),
          if (c.discovery['status'] == 'empty')
            const Text(
                'The endpoint answered but has no models installed. Odessy will not download one.'),
          if (c.discovery['status'] == 'unreachable')
            const Text(
                'The endpoint did not answer. Check that it is running; nothing was installed.'),
        ],
        const Divider(height: 32),
        SwitchListTile(
            title: const Text('Use existing voice components'),
            subtitle: const Text(
                'Optional. Requires your own Whisper.cpp executable and models plus Kokoro weights and voices. Nothing is downloaded.'),
            value: voice,
            onChanged: c.busy
                ? null
                : (value) {
                    setState(() => voice = value);
                    _edit();
                  }),
        if (voice) ...[
          _field(whisperBin, 'Whisper.cpp executable'),
          _field(whisperModel, 'Whisper model file'),
          _field(kokoroModel, 'Kokoro model file'),
          _field(kokoroVoices, 'Kokoro voices file'),
          if (_voiceInvalid)
            Text(
                'Voice is unavailable until all four existing asset paths are supplied, or turn it off to continue with text only.',
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
        ],
      ],
    ]);
  }

  Widget _review() {
    final review = c.review;
    final role = (c.selection['role'] as String?) ?? 'combined';
    final components = review['components'];
    return ListView(children: [
      Text('Review before installing',
          style: Theme.of(context).textTheme.titleLarge),
      const SizedBox(height: 10),
      _summaryRow('Role', role),
      _summaryRow('Install into', c.root),
      _summaryRow('Model',
          provider == 'later' ? 'Configure later — no model is started' : model.text.trim()),
      _summaryRow('Voice', voice && _voiceComplete ? 'Existing components' : 'Text only'),
      _summaryRow('Downloads', 'None. This is an offline package.'),
      _summaryRow('Service', 'Not created. Background start is a separate choice.'),
      const SizedBox(height: 12),
      if (review.isEmpty)
        FilledButton(
            onPressed: c.busy ? null : () => c.run('package-review'),
            child: const Text('Review this installation'))
      else ...[
        Text('Components', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 4),
        Wrap(spacing: 8, runSpacing: 8, children: [
          if (components is List)
            for (final item in components.whereType<String>())
              Chip(label: Text(item)),
        ]),
        const SizedBox(height: 14),
        ExpansionTile(
            tilePadding: EdgeInsets.zero,
            title: const Text('Technical details'),
            children: [
              SelectableText(const JsonEncoder.withIndent('  ').convert(review),
                  style: const TextStyle(fontSize: 12)),
            ]),
      ],
    ]);
  }

  Widget _summaryRow(String label, String value) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 120, child: Text(label)),
        Expanded(child: Text(value)),
      ]));

  Widget _progress() {
    final progress = c.progress;
    return ListView(children: [
      Text(c.stage.isEmpty ? c.status : 'Stage: ${c.stage}'),
      const SizedBox(height: 12),
      LinearProgressIndicator(value: progress),
      const SizedBox(height: 8),
      if (c.stepsTotal > 0)
        Text('${c.stepsDone} of ${c.stepsTotal} packaged steps completed'),
      const SizedBox(height: 16),
      if (c.error.isNotEmpty)
        SelectableText(c.error,
            style: TextStyle(color: Theme.of(context).colorScheme.error)),
      if (c.interrupted && !c.busy) ...[
        const SizedBox(height: 10),
        const Text(
            'The installation stopped before activation. A previous version, if any, stays active and no files were removed.'),
        FilledButton(
            onPressed: () {
              setState(() => step = 3);
              c.run('package-review');
            },
            child: const Text('Inspect and review recovery')),
      ],
      if (c.busy) ...[
        const SizedBox(height: 16),
        TextButton(onPressed: c.cancel, child: const Text('Cancel installation')),
      ],
    ]);
  }

  Widget _finish() {
    final result = c.result;
    final registration = (result['registration'] as String?) ?? 'skipped';
    final configured = provider != 'later' || c.context['selection'] != null;
    return ListView(children: [
      Text('Odessy is installed',
          style: Theme.of(context).textTheme.headlineSmall),
      const SizedBox(height: 10),
      _summaryRow('Location', c.root),
      _summaryRow('Readiness',
          configured ? 'Installed and configured — not yet verified' : 'Installed — model not configured'),
      _summaryRow('Start menu', registration == 'complete' || registration == 'skipped'
          ? 'Created'
          : 'Needs repair — use Manage installation'),
      if (result['registration_error'] != null)
        Text('${result['registration_error']}',
            style: TextStyle(color: Theme.of(context).colorScheme.error)),
      const SizedBox(height: 18),
      Wrap(spacing: 10, runSpacing: 10, children: [
        FilledButton.icon(
            onPressed: c.busy ? null : () => c.run('package-launch'),
            icon: const Icon(Icons.play_arrow, size: 18),
            label: const Text('Open Odessy')),
        OutlinedButton(
            onPressed: c.busy ? null : _openManagement,
            child: const Text('Manage installation')),
        TextButton(onPressed: _closeApplication, child: const Text('Close')),
      ]),
      const SizedBox(height: 18),
      ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: const Text('Installation details'),
          children: [
            SelectableText(const JsonEncoder.withIndent('  ').convert(result),
                style: const TextStyle(fontSize: 12)),
          ]),
    ]);
  }

  Widget _body() {
    if (c.context.isEmpty) {
      return Center(
          child: Text(c.error.isEmpty
              ? 'Verifying the installer package…'
              : 'The installer package could not be verified. ${c.reason}'));
    }
    return switch (step) {
      0 => _welcome(),
      1 => _machine(),
      2 => _modelAndVoice(),
      3 => _review(),
      4 => _progress(),
      _ => _finish(),
    };
  }

  static const _titles = [
    'Welcome',
    'This computer',
    'Model and voice',
    'Review',
    'Install',
    'Finish',
  ];

  bool get _canContinue => switch (step) {
        0 => true,
        1 => c.root.trim().isNotEmpty && !c.existingInstallation,
        2 => !_modelIncomplete && !_voiceInvalid,
        3 => c.review.isNotEmpty,
        _ => true,
      };

  @override
  Widget build(BuildContext context) {
    final manage = management;
    if (manage != null) {
      return MaterialApp(
          title: 'Manage Odessy',
          debugShowCheckedModeBanner: false,
          theme: odessyTheme(Brightness.dark),
          home: Scaffold(
              appBar: AppBar(
                  title: const Text('Manage Odessy'),
                  leading: IconButton(
                      tooltip: 'Back to setup',
                      icon: const Icon(Icons.arrow_back),
                      onPressed: () => setState(() => management = null))),
              body: ManagePage(
                  controller: manage, external: widget.payloadRoot.isNotEmpty)));
    }
    return MaterialApp(
        title: widget.mode == 'manage' ? 'Manage Odessy' : 'Odessy Setup',
        debugShowCheckedModeBanner: false,
        theme: odessyTheme(Brightness.dark),
        home: Scaffold(
            appBar: AppBar(
                title: Text(
                    widget.mode == 'manage' ? 'Manage Odessy' : 'Odessy Setup')),
            body: Padding(
                padding: const EdgeInsets.fromLTRB(32, 24, 32, 24),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      if (widget.mode != 'manage')
                        Text('${step + 1} of 6 · ${_titles[step]}',
                            style: Theme.of(context).textTheme.labelLarge),
                      const SizedBox(height: 18),
                      Expanded(child: _body()),
                      const SizedBox(height: 12),
                      if (c.error.isNotEmpty && step != 4 && step != 5)
                        Text(c.error,
                            style: TextStyle(
                                color: Theme.of(context).colorScheme.error)),
                      if (widget.mode != 'manage' && step < 4)
                        Row(children: [
                          TextButton(
                              onPressed: step == 0 || c.busy
                                  ? null
                                  : () => setState(() => step--),
                              child: const Text('Back')),
                          const Spacer(),
                          FilledButton(
                              onPressed: c.busy || !_canContinue
                                  ? null
                                  : () async {
                                      if (step == 3) {
                                        setState(() => step = 4);
                                        await c.run('package-install');
                                        if (mounted && c.installed) {
                                          setState(() => step = 5);
                                        }
                                      } else {
                                        setState(() => step++);
                                      }
                                    },
                              child: Text(step == 3 ? 'Install' : 'Continue')),
                        ]),
                      if (step == 5)
                        Row(children: [
                          TextButton(
                              onPressed: () => setState(() => step = 3),
                              child: const Text('Review details')),
                          const Spacer(),
                          TextButton(
                              onPressed: _closeApplication,
                              child: const Text('Close')),
                        ]),
                    ]))));
  }
}


