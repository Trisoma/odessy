import 'package:flutter/material.dart';

import '../controllers/voice_controller.dart';

/// Composer voice controls bound to an existing [VoiceController].
///
/// This widget owns no recorder, subscription or transcript. It only exposes the
/// controller's explicit actions (start/finish/cancel capture, wake enable and
/// disable, input and output tests, stop playback) together with their real
/// states. Building it never touches the microphone: the first capture happens
/// only when the user presses the microphone.
class ConversationVoiceControls extends StatefulWidget {
  const ConversationVoiceControls(
      {super.key, required this.controller, this.compact = false});
  final VoiceController controller;
  final bool compact;

  @override
  State<ConversationVoiceControls> createState() =>
      _ConversationVoiceControlsState();
}

class _ConversationVoiceControlsState extends State<ConversationVoiceControls> {
  bool options = false;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final theme = Theme.of(context),
              muted = theme.colorScheme.onSurfaceVariant;
          final active = const {
            'preparing',
            'recording',
            'transcribing',
            'speaking',
            'cancelling'
          }.contains(c.state);
          return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(children: [
                  IconButton(
                      key: const ValueKey('voice-mic'),
                      tooltip: _micTooltip(c),
                      onPressed: !c.supported
                          ? null
                          : c.recording && c.hasCapture
                              ? (c.busy
                                  ? null
                                  : () => c.command('finish_capture'))
                              : c.canStartCapture
                                  ? () => c.command('start_capture')
                                  : null,
                      icon: Icon(
                          c.recording
                              ? Icons.stop_circle_outlined
                              : Icons.mic_none,
                          size: 20)),
                  if (c.hasCapture)
                    IconButton(
                        key: const ValueKey('voice-cancel'),
                        tooltip: 'Cancel this capture without submitting',
                        onPressed: () => c.command('cancel_capture'),
                        icon: const Icon(Icons.close, size: 20)),
                  if (c.wakeMode)
                    IconButton(
                        key: const ValueKey('voice-wake-off'),
                        tooltip: 'Stop wake listening on the coordinator',
                        onPressed: c.canDisableWake
                            ? () => c.command('disable_wake')
                            : null,
                        icon: const Icon(Icons.hearing_disabled, size: 20)),
                  if (c.speaking)
                    IconButton(
                        key: const ValueKey('voice-stop-playback'),
                        tooltip: 'Stop speech playback',
                        onPressed: () => c.command('stop_playback'),
                        icon: const Icon(Icons.volume_off_outlined, size: 20)),
                  const Spacer(),
                  TextButton.icon(
                      key: const ValueKey('voice-options'),
                      style: TextButton.styleFrom(
                          visualDensity: VisualDensity.compact,
                          padding: const EdgeInsets.symmetric(horizontal: 8)),
                      onPressed: () => setState(() => options = !options),
                      icon: Icon(
                          options ? Icons.expand_less : Icons.expand_more,
                          size: 18),
                      label: const Text('Voice options')),
                ]),
                if (c.state != 'unavailable')
                  Semantics(
                      liveRegion: active,
                      child: Text(_statusLine(c),
                          key: const ValueKey('voice-status'),
                          style: theme.textTheme.bodySmall?.copyWith(
                              color: c.recording || c.transcribing
                                  ? theme.colorScheme.primary
                                  : muted))),
                if (c.error.isNotEmpty)
                  Semantics(
                      liveRegion: true,
                      child: Text(c.error,
                          key: const ValueKey('voice-error'),
                          style: theme.textTheme.bodySmall
                              ?.copyWith(color: theme.colorScheme.error))),
                if (c.transcript != null)
                  Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(
                          'Heard: ${c.transcript!.text} · already part of this session',
                          key: const ValueKey('voice-transcript'),
                          style: theme.textTheme.bodySmall
                              ?.copyWith(color: muted))),
                if (options) ...[
                  if (c.message.isNotEmpty)
                    Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text(c.message,
                            style: theme.textTheme.bodySmall
                                ?.copyWith(color: muted))),
                  Wrap(spacing: 8, runSpacing: 4, children: [
                    TextButton(
                        key: const ValueKey('voice-check'),
                        onPressed: c.refresh,
                        child: const Text('Check voice availability')),
                    if (!c.wakeMode)
                      TextButton(
                          key: const ValueKey('voice-wake-on'),
                          onPressed: c.canEnableWake
                              ? () => c.command('enable_wake')
                              : null,
                          child: const Text(
                              'Enable wake listening on coordinator')),
                    TextButton(
                        key: const ValueKey('voice-test-input'),
                        onPressed: c.canTestInput
                            ? () => c.command('test_input')
                            : null,
                        child: const Text('Test speech input · no model call')),
                    TextButton(
                        key: const ValueKey('voice-test-output'),
                        onPressed: c.canTestOutput
                            ? () => c.command('test_output')
                            : null,
                        child: const Text('Test speech output')),
                    TextButton(
                        key: const ValueKey('voice-stop-playback-option'),
                        onPressed: () => c.command('stop_playback'),
                        child: const Text('Stop playback')),
                  ]),
                ],
                if (!c.supported)
                  Text(
                      'Voice is unavailable on this connection; typed input remains available.',
                      style: theme.textTheme.bodySmall?.copyWith(color: muted)),
              ]);
        },
      );

  String _micTooltip(VoiceController c) {
    if (!c.supported) return 'Voice is unavailable; type instead';
    if (c.recording) return 'Finish recording and submit the transcript';
    if (c.wakeMode) {
      return 'Wake listening owns the microphone; turn it off first';
    }
    return 'Record on the coordinator computer';
  }

  String _statusLine(VoiceController c) {
    final host = c.snapshot?.host ?? 'coordinator computer';
    return switch (c.state) {
      'recording' => 'Recording on $host · finish to transcribe',
      'preparing' => 'Preparing audio on $host',
      'transcribing' => 'Transcribing on $host · text is not sent again here',
      'speaking' => 'Speaking on $host',
      'cancelling' => 'Cancelling audio',
      'listening' => 'Wake listening on $host',
      'idle' => c.wakeMode
          ? 'Wake listening on $host'
          : 'Voice idle · microphone is not accessed',
      'error' => 'Voice error · typed input remains available',
      'busy' => 'Another session owns audio',
      _ => 'Voice unavailable',
    };
  }
}
