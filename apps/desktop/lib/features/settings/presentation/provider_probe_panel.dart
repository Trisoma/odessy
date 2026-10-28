import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/setup/native_picker.dart';
import '../controllers/provider_probe_controller.dart';

class ProviderProbePanel extends StatefulWidget {
  const ProviderProbePanel(
      {super.key,
      required this.backend,
      required this.selection,
      required this.section,
      required this.picker,
      this.onDevice});
  final SetupBackend backend;
  final Json selection;
  final String section;
  final NativePicker picker;
  final void Function(String, String)? onDevice;
  @override
  State<ProviderProbePanel> createState() => _ProviderProbePanelState();
}

class _ProviderProbePanelState extends State<ProviderProbePanel> {
  late final c = ProviderProbeController(widget.backend);
  String audio = '';
  @override
  void didUpdateWidget(covariant ProviderProbePanel old) {
    super.didUpdateWidget(old);
    if (jsonEncode(old.selection) != jsonEncode(widget.selection)) {
      c.invalidate();
    }
  }

  @override
  void dispose() {
    c.dispose();
    super.dispose();
  }

  Future<void> test() async {
    final accepted = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
                title: const Text('Run this component test?'),
                content: Text(widget.section == 'llm'
                    ? 'Send a fixed short greeting to this model. Cloud usage may be billed. This is a separate diagnostic run with its own usage/budget; no tools, conversation history, failover or automatic retry.'
                    : widget.section == 'tts'
                        ? 'Synthesize a fixed short sentence and play it on this computer. A custom provider may contact its configured service. No LLM is used.'
                        : audio.isEmpty
                            ? 'Record five seconds from this computer’s selected microphone, then transcribe. Speak immediately after Run test. No LLM is used; the temporary recording is removed. A custom provider may contact its configured service.'
                            : 'Transcribe the selected WAV on this computer. A custom provider may contact its configured service. No LLM is used.'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context, false),
                      child: const Text('Cancel')),
                  FilledButton(
                      onPressed: () => Navigator.pop(context, true),
                      child: const Text('Run test'))
                ]));
    if (accepted == true && mounted) {
      await c.run(widget.selection, widget.section, test: true, audio: audio);
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: c,
      builder: (context, _) =>
          Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const SizedBox(height: 20),
            const Divider(),
            const Text('Component checks'),
            const Text(
                'Checks use the values shown here. Editing clears the result. Saving does not run a test; passing does not prove tool compatibility.'),
            if (widget.section == 'stt')
              Wrap(
                  spacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    OutlinedButton(
                        onPressed: c.busy
                            ? null
                            : () async {
                                final path = await widget.picker
                                    .pickFile(extensions: const ['wav']);
                                if (path != null && mounted) {
                                  setState(() {
                                    audio = path;
                                    c.invalidate();
                                  });
                                }
                              },
                        child: const Text('Choose test WAV')),
                    Text(
                        audio.isEmpty ? 'Use microphone · five seconds' : audio,
                        overflow: TextOverflow.ellipsis,
                        maxLines: 1),
                    if (audio.isNotEmpty)
                      TextButton(
                          onPressed: c.busy
                              ? null
                              : () => setState(() {
                                    audio = '';
                                    c.invalidate();
                                  }),
                          child: const Text('Use microphone instead')),
                  ]),
            Wrap(spacing: 12, runSpacing: 8, children: [
              OutlinedButton(
                  onPressed: c.busy
                      ? null
                      : () => c.run(widget.selection, widget.section),
                  child: Text(widget.section == 'voice'
                      ? 'Refresh audio devices'
                      : 'Check configuration')),
              if (['llm', 'stt', 'tts'].contains(widget.section))
                OutlinedButton(
                    onPressed: c.busy ? null : test,
                    child: Text(widget.section == 'llm'
                        ? 'Test model'
                        : widget.section == 'tts'
                            ? 'Test speech output'
                            : 'Test transcription')),
              if (c.busy)
                TextButton(
                    onPressed: c.cancel, child: const Text('Cancel test')),
            ]),
            if (c.busy)
              const LinearProgressIndicator(
                  semanticsLabel: 'Component test in progress'),
            if (c.error.isNotEmpty)
              Semantics(
                  liveRegion: true,
                  child: Text(c.error,
                      semanticsLabel: 'Component check failed: ${c.error}')),
            if (c.result.isNotEmpty) ...[
              Semantics(
                  liveRegion: true,
                  child: Text(
                      '${c.result['status']}: ${c.result['message'] ?? ''}')),
              if (c.result['elapsed_ms'] != null)
                Text('Elapsed: ${c.result['elapsed_ms']} ms'),
              if (c.result['tested_at'] is num)
                Text(
                    'Tested: ${DateTime.fromMillisecondsSinceEpoch(((c.result['tested_at'] as num) * 1000).round()).toLocal()} · displayed configuration only'),
              if (c.result['model'] is String)
                Text(
                    'Tested model: ${c.result['provider']} / ${c.result['model']}'),
              if (c.result['text'] is String)
                SelectableText(c.result['text'] as String),
              if (c.result['usage'] is Map)
                Text(
                    'Diagnostic usage: ${(c.result['usage'] as Map)['prompt']} input / ${(c.result['usage'] as Map)['completion']} output tokens. Price is unknown unless configured.'),
              for (final device in (c.result['devices'] as List? ?? []))
                Wrap(
                    spacing: 8,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      Text('${device['name']}'),
                      if (device['input'] == true)
                        TextButton(
                            onPressed: () => widget.onDevice?.call(
                                'audio_input_device', device['id'] as String),
                            child: const Text('Use microphone')),
                      if (device['output'] == true)
                        TextButton(
                            onPressed: () => widget.onDevice?.call(
                                'audio_output_device', device['id'] as String),
                            child: const Text('Use speaker')),
                    ]),
            ],
          ]));
}
