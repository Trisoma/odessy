import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/session_controller.dart';
import 'package:odessy_desktop/features/conversation/controllers/voice_controller.dart';

class AudioApi extends DemoApi implements DesktopVoiceApi {
  final commands = <String>[];
  String state = 'idle';
  bool fail = false;
  VoiceSnapshot get value => VoiceSnapshot.fromJson({
        'supported': true,
        'state': state,
        'capture_id': state == 'recording' ? 'capture' : null,
        'recorder_host': 'coordinator-test'
      });
  @override
  Future<VoiceSnapshot> voiceState(String sid) async => value;
  @override
  Future<VoiceSnapshot> voiceCommand(String sid, String action,
      {String? captureId, String? interactionId}) async {
    commands.add(action);
    if (fail) throw const ApiFailure(503);
    if (action == 'finish_capture') expect(captureId, 'capture');
    state = action == 'start_capture' ? 'recording' : 'idle';
    return value;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test(
      'explicit capture uses current session and cancellation never submits text',
      () async {
    final api = AudioApi(), session = DesktopSession(AudioApi());
    await session.replace(api);
    final voice = VoiceController(session);
    await voice.refresh();
    expect(api.commands, isEmpty);
    await voice.command('start_capture');
    expect(voice.recording, isTrue);
    await voice.command('cancel_capture');
    expect(voice.recording, isFalse);
    expect(session.session!.messages, isEmpty);
    await voice.command('start_capture');
    await voice.command('finish_capture');
    expect(api.commands,
        ['start_capture', 'cancel_capture', 'start_capture', 'finish_capture']);
    voice.dispose();
    await session.shutdown();
    session.dispose();
  });
  test('audio failure does not change transcript or disable typed session',
      () async {
    final api = AudioApi(), session = DesktopSession(AudioApi());
    await session.replace(api);
    final voice = VoiceController(session);
    await voice.refresh();
    api.fail = true;
    await voice.command('start_capture');
    expect(voice.error, isNotEmpty);
    expect(session.connected, isTrue);
    expect(session.session!.messages, isEmpty);
    voice.dispose();
    await session.shutdown();
    session.dispose();
  });
}
