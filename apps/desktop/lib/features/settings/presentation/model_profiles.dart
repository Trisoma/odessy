import 'package:flutter/material.dart';
import '../../../app/session_controller.dart';
import '../controllers/model_profiles_controller.dart';

class ModelProfiles extends StatefulWidget {
  const ModelProfiles({super.key, required this.session, this.onDialogChange});
  final DesktopSession session;
  final ValueChanged<bool>? onDialogChange;
  @override
  State<ModelProfiles> createState() => _ModelProfilesState();
}

class _ModelProfilesState extends State<ModelProfiles> {
  late ModelProfilesController controller;
  bool catalogLoading = false;
  @override
  void initState() {
    super.initState();
    controller = ModelProfilesController(widget.session);
  }

  @override
  void didUpdateWidget(covariant ModelProfiles oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.session != widget.session) {
      controller.dispose();
      controller = ModelProfilesController(widget.session);
    }
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  Future<void> _open(Widget dialog) async {
    widget.onDialogChange?.call(true);
    try {
      await showDialog<void>(
          context: context, barrierDismissible: false, builder: (_) => dialog);
    } finally {
      widget.onDialogChange?.call(false);
    }
  }

  Future<void> _create() async {
    if (catalogLoading) return;
    setState(() => catalogLoading = true);
    final original = controller;
    final version = original.document;
    final providers = await original.providers();
    if (mounted) setState(() => catalogLoading = false);
    if (!mounted ||
        !identical(original, controller) ||
        providers == null ||
        version == null ||
        !identical(version, original.document)) {
      return;
    }
    await _open(_CreateProfileDialog(
        controller: controller, providers: providers, version: version));
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final profiles = c.document?['profiles'] as List? ?? const [];
        return Card(
            child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text('Saved model profiles',
                          style: Theme.of(context).textTheme.titleMedium),
                      const Text(
                          'Manage cloud or Ollama model profiles. Changes apply to new sessions; credentials are configured outside this screen.'),
                      if (!c.available)
                        const Text(
                            'Model profile editing is unavailable for this connection.'),
                      if (c.loading || c.saving)
                        const LinearProgressIndicator(
                            semanticsLabel: 'Loading model profiles'),
                      if (c.error != null) Text(c.error!),
                      Align(
                          alignment: Alignment.centerLeft,
                          child: OutlinedButton.icon(
                              onPressed: c.available && !c.loading && !c.saving
                                  ? c.refresh
                                  : null,
                              icon: const Icon(Icons.refresh),
                              label: const Text('Refresh profiles'))),
                      Align(
                          alignment: Alignment.centerLeft,
                          child: OutlinedButton.icon(
                              onPressed: c.canManage &&
                                      c.document != null &&
                                      !c.loading &&
                                      !c.saving &&
                                      !catalogLoading
                                  ? _create
                                  : null,
                              icon: const Icon(Icons.add),
                              label: const Text('Add model profile'))),
                      if (c.document != null && profiles.isEmpty)
                        const Text(
                            'No editable model profiles are configured.'),
                      for (final value in profiles)
                        ListTile(
                            title: Text(value['name'] as String),
                            subtitle:
                                Text('${value['adapter']} · ${value['model']}'),
                            trailing: const Icon(Icons.edit_outlined),
                            onTap: c.saving || c.loading
                                ? null
                                : () => _open(_ProfileDialog(
                                    controller: c,
                                    profile: value as Map,
                                    version: c.document!))),
                    ])));
      });
}

class _ProfileDialog extends StatefulWidget {
  const _ProfileDialog(
      {required this.controller, required this.profile, required this.version});
  final ModelProfilesController controller;
  final Map profile;
  final Map<String, dynamic> version;
  @override
  State<_ProfileDialog> createState() => _ProfileDialogState();
}

class _ProfileDialogState extends State<_ProfileDialog> {
  late final TextEditingController model, endpoint;
  bool pending = false;
  String? error;
  @override
  void initState() {
    super.initState();
    model = TextEditingController(text: widget.profile['model'] as String);
    endpoint =
        TextEditingController(text: widget.profile['endpoint'] as String);
  }

  @override
  void dispose() {
    model.dispose();
    endpoint.dispose();
    super.dispose();
  }

  Future<void> save() async {
    setState(() {
      pending = true;
      error = null;
    });
    final ok = await widget.controller.save(widget.profile['name'] as String,
        model.text, endpoint.text, widget.version);
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop();
      return;
    }
    setState(() {
      pending = false;
      error =
          'Save was not confirmed. Close and refresh profiles before retrying.';
    });
  }

  Future<void> remove() async {
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
                title: const Text('Delete model profile?'),
                content: Text(
                    'Delete ${widget.profile['name']} from saved configuration? Existing sessions keep their current configuration.'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context, false),
                      child: const Text('Cancel')),
                  FilledButton(
                      onPressed: () => Navigator.pop(context, true),
                      child: const Text('Delete profile'))
                ]));
    if (!mounted || confirmed != true) return;
    setState(() {
      pending = true;
      error = null;
    });
    final ok = await widget.controller
        .remove(widget.profile['name'] as String, widget.version);
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop();
      return;
    }
    setState(() {
      pending = false;
      error =
          'Delete was not confirmed. The profile may be referenced by collaboration settings. Close and refresh before retrying.';
    });
  }

  Future<void> switchProvider() async {
    setState(() {
      pending = true;
    });
    final providers = await widget.controller.providers();
    if (!mounted) return;
    setState(() {
      pending = false;
    });
    if (providers == null ||
        !identical(widget.version, widget.controller.document)) {
      setState(() {
        error =
            'Provider catalog or configuration changed. Close and refresh before retrying.';
      });
      return;
    }
    final result = await showDialog<bool>(
        context: context,
        barrierDismissible: false,
        builder: (_) => _ProviderSwitchDialog(
            controller: widget.controller,
            profile: widget.profile,
            version: widget.version,
            providers: providers));
    if (!mounted) return;
    if (result == true) {
      Navigator.of(context).pop();
      return;
    }
    if (result == false) {
      setState(() {
        error =
            'Provider change was not confirmed. Close and refresh before retrying.';
      });
    }
  }

  Future<void> editPricing() async {
    setState(() {
      pending = true;
    });
    final pricing = await widget.controller
        .pricing(widget.profile['name'] as String, widget.version);
    if (!mounted) return;
    setState(() {
      pending = false;
    });
    if (pricing == null) {
      setState(() {
        error =
            'Pricing or configuration changed. Close and refresh before retrying.';
      });
      return;
    }
    final result = await showDialog<bool>(
        context: context,
        barrierDismissible: false,
        builder: (_) => _PricingDialog(
            controller: widget.controller,
            profile: widget.profile,
            version: widget.version,
            pricing: pricing['pricing'] as Map?));
    if (!mounted) return;
    if (result == true) {
      Navigator.of(context).pop();
      return;
    }
    if (result == false) {
      setState(() {
        error =
            'Pricing change was not confirmed. Close and refresh before retrying.';
      });
    }
  }

  Future<void> editLimits() async {
    setState(() => pending = true);
    final limits = await widget.controller
        .limits(widget.profile['name'] as String, widget.version);
    if (!mounted) return;
    setState(() => pending = false);
    if (limits == null) {
      setState(() => error =
          'Model limits are unavailable or changed. Close and refresh before retrying.');
      return;
    }
    final result = await showDialog<bool>(
        context: context,
        barrierDismissible: false,
        builder: (_) => _LimitsDialog(
            controller: widget.controller,
            profile: widget.profile,
            version: widget.version,
            limits: limits));
    if (!mounted) return;
    if (result == true) Navigator.of(context).pop();
    if (result == false) {
      setState(() =>
          error = 'Save was not confirmed. Close and refresh before retrying.');
    }
  }

  Future<void> editCredentialReference() async {
    setState(() {
      pending = true;
    });
    final reference = await widget.controller
        .credentialReference(widget.profile['name'] as String, widget.version);
    if (!mounted) return;
    setState(() {
      pending = false;
    });
    if (reference == null) {
      setState(() {
        error =
            'Credential reference or configuration changed. Close and refresh before retrying.';
      });
      return;
    }
    final result = await showDialog<bool>(
        context: context,
        barrierDismissible: false,
        builder: (_) => _CredentialReferenceDialog(
            controller: widget.controller,
            profile: widget.profile,
            version: widget.version,
            reference: reference));
    if (!mounted) return;
    if (result == true) {
      Navigator.of(context).pop();
      return;
    }
    if (result == false) {
      setState(() {
        error =
            'Credential reference change was not confirmed. Close and refresh before retrying.';
      });
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
      canPop: !pending,
      child: AlertDialog(
          title: Text('Edit ${widget.profile['name']}'),
          content: SingleChildScrollView(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Text(
                'Leave a field empty to use its configured default. The endpoint must be an HTTP or HTTPS URL without credentials or query parameters.'),
            TextField(
                controller: model,
                enabled: !pending && error == null,
                maxLength: 4096,
                decoration: const InputDecoration(labelText: 'Model name')),
            TextField(
                controller: endpoint,
                enabled: !pending && error == null,
                maxLength: 4096,
                decoration:
                    const InputDecoration(labelText: 'Service endpoint')),
            if (error != null) Text(error!),
          ])),
          actions: [
            if (widget.controller.canSetLimits)
              TextButton(
                  onPressed: pending || error != null ? null : editLimits,
                  child: const Text('Request limits…')),
            if (widget.profile['adapter'] == 'cloud' &&
                widget.controller.canReferenceCredentials)
              TextButton(
                  onPressed:
                      pending || error != null ? null : editCredentialReference,
                  child: const Text('Credential reference…')),
            if (widget.profile['adapter'] == 'cloud' &&
                widget.controller.canPrice)
              TextButton(
                  onPressed: pending || error != null ? null : editPricing,
                  child: const Text('Pricing…')),
            if (widget.profile['adapter'] == 'cloud' &&
                widget.controller.canSwitch)
              TextButton(
                  onPressed: pending || error != null ? null : switchProvider,
                  child: const Text('Change provider…')),
            if (widget.controller.canManage)
              TextButton(
                  onPressed: pending || error != null ? null : remove,
                  child: const Text('Delete…')),
            TextButton(
                onPressed: pending ? null : () => Navigator.of(context).pop(),
                child: const Text('Cancel')),
            FilledButton(
                onPressed: pending || error != null ? null : save,
                child: Text(pending ? 'Saving…' : 'Save'))
          ]));
}

class _LimitsDialog extends StatefulWidget {
  const _LimitsDialog(
      {required this.controller,
      required this.profile,
      required this.version,
      required this.limits});
  final ModelProfilesController controller;
  final Map profile;
  final Map<String, dynamic> version, limits;
  @override
  State<_LimitsDialog> createState() => _LimitsDialogState();
}

class _LimitsDialogState extends State<_LimitsDialog> {
  late final TextEditingController timeout, tokens;
  bool pending = false;
  String? error;
  @override
  void initState() {
    super.initState();
    timeout = TextEditingController(text: '${widget.limits['timeout']}');
    tokens = TextEditingController(text: '${widget.limits['max_tokens']}');
  }

  @override
  void dispose() {
    timeout.dispose();
    tokens.dispose();
    super.dispose();
  }

  Future<void> save() async {
    final seconds = int.tryParse(timeout.text.trim()),
        maximum = int.tryParse(tokens.text.trim());
    if (seconds == null ||
        seconds < 1 ||
        seconds > 3600 ||
        maximum == null ||
        maximum < 1 ||
        maximum > 1000000) {
      setState(() => error =
          'Enter a timeout from 1 to 3600 seconds and an output limit from 1 to 1000000 tokens.');
      return;
    }
    setState(() {
      pending = true;
      error = null;
    });
    final result = await widget.controller.saveLimits(
        widget.profile['name'] as String, seconds, maximum, widget.version);
    if (mounted) Navigator.of(context).pop(result);
  }

  @override
  Widget build(BuildContext context) => PopScope(
      canPop: !pending,
      child: AlertDialog(
        title: const Text('Model request limits'),
        content: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Text(
              'Changes apply to new sessions. Saving closes the profile editor and discards its other unsaved edits.'),
          TextField(
              controller: timeout,
              enabled: !pending,
              keyboardType: TextInputType.number,
              decoration:
                  const InputDecoration(labelText: 'Timeout (seconds)')),
          TextField(
              controller: tokens,
              enabled: !pending,
              keyboardType: TextInputType.number,
              decoration:
                  const InputDecoration(labelText: 'Maximum output tokens')),
          if (error != null) Text(error!),
        ])),
        actions: [
          TextButton(
              onPressed: pending ? null : () => Navigator.pop(context),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: pending ? null : save,
              child: const Text('Save limits'))
        ],
      ));
}

class _CredentialReferenceDialog extends StatefulWidget {
  const _CredentialReferenceDialog(
      {required this.controller,
      required this.profile,
      required this.version,
      required this.reference});
  final ModelProfilesController controller;
  final Map profile;
  final Map<String, dynamic> version, reference;
  @override
  State<_CredentialReferenceDialog> createState() =>
      _CredentialReferenceDialogState();
}

class _CredentialReferenceDialogState
    extends State<_CredentialReferenceDialog> {
  late final TextEditingController name;
  bool pending = false, failed = false;
  String? error;
  @override
  void initState() {
    super.initState();
    name = TextEditingController(
        text: widget.reference['credential_env'] as String);
  }

  @override
  void dispose() {
    name.dispose();
    super.dispose();
  }

  Future<void> save() async {
    final value = name.text.trim();
    if (value.isNotEmpty &&
        (!RegExp(r'^[A-Z][A-Z0-9_]{0,110}_(API_KEY|ACCESS_TOKEN)$')
                .hasMatch(value) ||
            (value.startsWith('ODESSY_') && value != 'ODESSY_CLOUD_API_KEY'))) {
      setState(() => error =
          'Enter an uppercase variable ending in _API_KEY or _ACCESS_TOKEN. Internal ODESSY names are reserved.');
      return;
    }
    setState(() {
      pending = true;
      error = null;
    });
    final ok = await widget.controller.saveCredentialReference(
        widget.profile['name'] as String, value, widget.version);
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop(true);
      return;
    }
    setState(() {
      pending = false;
      failed = true;
      error =
          'Change was not confirmed. Settings may have changed. Close and refresh.';
    });
  }

  @override
  Widget build(BuildContext context) => PopScope(
      canPop: !pending,
      child: AlertDialog(
          title: const Text('Credential reference'),
          content: SingleChildScrollView(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Text(
                'Enter an environment variable name only. Do not paste the credential value. The service does not display or store the secret here.'),
            Text(
                'Provider default: ${widget.reference['default_credential_env']}'),
            const Text(
                'Leave empty to use the provider default. Set the actual credential separately in the service environment; a service restart may be needed. Changes apply to new sessions.'),
            TextField(
                controller: name,
                enabled: !pending && !failed,
                maxLength: 128,
                decoration: const InputDecoration(
                    labelText: 'Credential environment variable')),
            if (error != null) Text(error!),
          ])),
          actions: [
            TextButton(
                onPressed: pending
                    ? null
                    : () => Navigator.of(context).pop(failed ? false : null),
                child: const Text('Cancel')),
            FilledButton(
                onPressed: pending || failed ? null : save,
                child: Text(pending ? 'Saving…' : 'Save reference'))
          ]));
}

class _PricingDialog extends StatefulWidget {
  const _PricingDialog(
      {required this.controller,
      required this.profile,
      required this.version,
      required this.pricing});
  final ModelProfilesController controller;
  final Map profile;
  final Map<String, dynamic> version;
  final Map? pricing;
  @override
  State<_PricingDialog> createState() => _PricingDialogState();
}

class _PricingDialogState extends State<_PricingDialog> {
  late final TextEditingController input, cached, output, currency;
  bool pending = false, failed = false;
  String? error;
  @override
  void initState() {
    super.initState();
    String value(String key) => widget.pricing?[key]?.toString() ?? '';
    input = TextEditingController(text: value('input'));
    cached = TextEditingController(text: value('cached_input'));
    output = TextEditingController(text: value('output'));
    currency = TextEditingController(text: value('currency'));
  }

  @override
  void dispose() {
    input.dispose();
    cached.dispose();
    output.dispose();
    currency.dispose();
    super.dispose();
  }

  Future<void> save({bool clear = false}) async {
    Map<String, dynamic>? pricing;
    if (!clear) {
      final i = double.tryParse(input.text.trim()),
          c = double.tryParse(cached.text.trim()),
          o = double.tryParse(output.text.trim());
      if ([i, c, o].any((value) =>
              value == null || !value.isFinite || value < 0 || value > 1e12) ||
          !RegExp(r'^[A-Z]{3,8}$').hasMatch(currency.text.trim())) {
        setState(() => error =
            'Enter nonnegative finite prices and a 3–8 letter uppercase currency.');
        return;
      }
      pricing = {
        'input': i,
        'cached_input': c,
        'output': o,
        'currency': currency.text.trim()
      };
    }
    setState(() {
      pending = true;
      error = null;
    });
    final ok = await widget.controller
        .savePricing(widget.profile['name'] as String, pricing, widget.version);
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop(true);
      return;
    }
    setState(() {
      pending = false;
      failed = true;
      error =
          'Change was not confirmed. Currency may conflict with other prices or settings may have changed. Close and refresh.';
    });
  }

  Future<void> clear() async {
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
                title: const Text('Clear profile pricing?'),
                content: const Text(
                    'Future estimates for this profile will be unknown until prices are configured. A cost budget may prevent calls when prices are unknown.'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context, false),
                      child: const Text('Cancel')),
                  FilledButton(
                      onPressed: () => Navigator.pop(context, true),
                      child: const Text('Clear pricing'))
                ]));
    if (mounted && confirmed == true) await save(clear: true);
  }

  @override
  Widget build(BuildContext context) => PopScope(
      canPop: !pending,
      child: AlertDialog(
          title: const Text('Profile pricing'),
          content: SingleChildScrollView(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Text(
                'Enter your own prices per million tokens. Estimates are not bills. Restart the local service before using changed prices; existing sessions retain their configuration.'),
            const Text(
                'All configured prices in the service must use the same currency.'),
            if (widget.pricing == null)
              const Text('No profile prices are configured.'),
            for (final field in [
              (input, 'Input price'),
              (cached, 'Cached input price'),
              (output, 'Output price'),
              (currency, 'Currency')
            ])
              TextField(
                  controller: field.$1,
                  enabled: !pending && !failed,
                  decoration: InputDecoration(labelText: field.$2)),
            if (error != null) Text(error!),
          ])),
          actions: [
            if (widget.pricing != null)
              TextButton(
                  onPressed: pending || failed ? null : clear,
                  child: const Text('Clear…')),
            TextButton(
                onPressed: pending
                    ? null
                    : () => Navigator.of(context).pop(failed ? false : null),
                child: const Text('Cancel')),
            FilledButton(
                onPressed: pending || failed ? null : save,
                child: Text(pending ? 'Saving…' : 'Save prices'))
          ]));
}

class _ProviderSwitchDialog extends StatefulWidget {
  const _ProviderSwitchDialog(
      {required this.controller,
      required this.profile,
      required this.version,
      required this.providers});
  final ModelProfilesController controller;
  final Map profile;
  final Map<String, dynamic> version;
  final List<Map<String, dynamic>> providers;
  @override
  State<_ProviderSwitchDialog> createState() => _ProviderSwitchDialogState();
}

class _ProviderSwitchDialogState extends State<_ProviderSwitchDialog> {
  final model = TextEditingController(), endpoint = TextEditingController();
  String? provider, error;
  bool pending = false, failed = false;
  @override
  void dispose() {
    model.dispose();
    endpoint.dispose();
    super.dispose();
  }

  Future<void> save() async {
    if (provider == null) {
      setState(() => error = 'Select a different provider.');
      return;
    }
    if (provider == 'custom' &&
        (model.text.trim().isEmpty || endpoint.text.trim().isEmpty)) {
      setState(() => error = 'Custom providers require a model and endpoint.');
      return;
    }
    setState(() {
      pending = true;
      error = null;
    });
    final ok = await widget.controller.switchProvider(
        widget.profile['name'] as String,
        provider!,
        model.text,
        endpoint.text,
        widget.version);
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop(true);
      return;
    }
    setState(() {
      pending = false;
      failed = true;
      error =
          'Change was not confirmed. The profile may have a custom credential reference or the configuration may have changed. Close and refresh.';
    });
  }

  @override
  Widget build(BuildContext context) {
    final original = (widget.profile['provider'] as String).isEmpty
        ? 'openai'
        : widget.profile['provider'];
    final choices =
        widget.providers.where((item) => item['name'] != original).toList();
    final selected =
        choices.where((item) => item['name'] == provider).firstOrNull;
    return PopScope(
        canPop: !pending,
        child: AlertDialog(
            title: const Text('Change cloud provider'),
            content: SingleChildScrollView(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Text(
                  'Changing provider replaces the old model and endpoint and clears its price settings. Unsaved edits in the previous form are discarded. Changes apply to new sessions.'),
              const Text(
                  'The service uses the new provider credential environment variable. Profiles with custom credential references cannot be switched here.'),
              DropdownButtonFormField<String>(
                  initialValue: provider,
                  decoration: const InputDecoration(labelText: 'New provider'),
                  items: [
                    for (final item in choices)
                      DropdownMenuItem(
                          value: item['name'] as String,
                          child: Text(item['name'] as String))
                  ],
                  onChanged: pending || failed
                      ? null
                      : (value) => setState(() {
                            provider = value;
                            error = null;
                          })),
              if (selected != null)
                Text(
                    'Service credential environment variable: ${selected['credential_env']}'),
              TextField(
                  controller: model,
                  enabled: !pending && !failed,
                  maxLength: 4096,
                  decoration: const InputDecoration(
                      labelText: 'New model name (optional)')),
              TextField(
                  controller: endpoint,
                  enabled: !pending && !failed,
                  maxLength: 4096,
                  decoration: const InputDecoration(
                      labelText: 'New service endpoint (optional)')),
              if (error != null) Text(error!),
            ])),
            actions: [
              TextButton(
                  onPressed: pending
                      ? null
                      : () => Navigator.of(context).pop(failed ? false : null),
                  child: const Text('Cancel')),
              FilledButton(
                  onPressed: pending || failed ? null : save,
                  child: Text(pending ? 'Changing…' : 'Change provider'))
            ]));
  }
}

class _CreateProfileDialog extends StatefulWidget {
  const _CreateProfileDialog(
      {required this.controller,
      required this.providers,
      required this.version});
  final ModelProfilesController controller;
  final List<Map<String, dynamic>> providers;
  final Map<String, dynamic> version;
  @override
  State<_CreateProfileDialog> createState() => _CreateProfileDialogState();
}

class _CreateProfileDialogState extends State<_CreateProfileDialog> {
  final name = TextEditingController(),
      model = TextEditingController(),
      endpoint = TextEditingController();
  String adapter = 'ollama';
  String? provider;
  bool pending = false;
  String? error;
  @override
  void initState() {
    super.initState();
    if (widget.providers.isNotEmpty) {
      provider = widget.providers.first['name'] as String;
    }
  }

  @override
  void dispose() {
    name.dispose();
    model.dispose();
    endpoint.dispose();
    super.dispose();
  }

  Future<void> save() async {
    final profileName = name.text.trim();
    if (!RegExp(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$').hasMatch(profileName) ||
        const {'current', 'auto', 'local'}.contains(profileName)) {
      setState(() => error =
          'Use 1–64 letters, digits, dots, dashes or underscores; built-in names are reserved.');
      return;
    }
    if (adapter == 'cloud' &&
        (provider == null ||
            (provider == 'custom' &&
                (model.text.trim().isEmpty || endpoint.text.trim().isEmpty)))) {
      setState(() => error =
          'Select a provider. Custom providers require a model and endpoint.');
      return;
    }
    setState(() {
      pending = true;
      error = null;
    });
    final ok = await widget.controller.create(
        profileName,
        adapter,
        adapter == 'cloud' ? provider! : '',
        model.text,
        endpoint.text,
        widget.version);
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop();
      return;
    }
    setState(() {
      pending = false;
      error = 'Creation was not confirmed. Close and refresh before retrying.';
    });
  }

  @override
  Widget build(BuildContext context) {
    final providerInfo =
        widget.providers.where((item) => item['name'] == provider).firstOrNull;
    return PopScope(
        canPop: !pending,
        child: AlertDialog(
            title: const Text('Add model profile'),
            content: SingleChildScrollView(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Text(
                  'The profile is available to new sessions. Cloud credentials must already be available to the service.'),
              TextField(
                  controller: name,
                  enabled: !pending,
                  maxLength: 64,
                  decoration: const InputDecoration(labelText: 'Profile name')),
              DropdownButtonFormField<String>(
                  initialValue: adapter,
                  decoration: const InputDecoration(labelText: 'Adapter'),
                  items: const [
                    DropdownMenuItem(value: 'ollama', child: Text('Ollama')),
                    DropdownMenuItem(value: 'cloud', child: Text('Cloud'))
                  ],
                  onChanged: pending
                      ? null
                      : (value) => setState(() {
                            adapter = value!;
                            error = null;
                          })),
              if (adapter == 'cloud') ...[
                DropdownButtonFormField<String>(
                    initialValue: provider,
                    decoration: const InputDecoration(labelText: 'Provider'),
                    items: [
                      for (final item in widget.providers)
                        DropdownMenuItem(
                            value: item['name'] as String,
                            child: Text(item['name'] as String))
                    ],
                    onChanged: pending
                        ? null
                        : (value) => setState(() {
                              provider = value;
                              error = null;
                            })),
                if (providerInfo != null)
                  Text(
                      'Service credential environment variable: ${providerInfo['credential_env']}'),
              ],
              TextField(
                  controller: model,
                  enabled: !pending,
                  maxLength: 4096,
                  decoration: const InputDecoration(
                      labelText: 'Model name (optional)')),
              TextField(
                  controller: endpoint,
                  enabled: !pending,
                  maxLength: 4096,
                  decoration: const InputDecoration(
                      labelText: 'Service endpoint (optional)')),
              if (error != null) Text(error!),
            ])),
            actions: [
              TextButton(
                  onPressed: pending ? null : () => Navigator.of(context).pop(),
                  child: const Text('Cancel')),
              FilledButton(
                  onPressed: pending ||
                          (error?.startsWith('Creation was not confirmed') ??
                              false)
                      ? null
                      : save,
                  child: Text(pending ? 'Creating…' : 'Create profile'))
            ]));
  }
}
