import 'models.dart';

abstract interface class DesktopVoiceApi {
  Future<VoiceSnapshot> voiceState(String session);
  Future<VoiceSnapshot> voiceCommand(String session,String action,{String? captureId,String? interactionId});
}
class VoiceSnapshot {
  VoiceSnapshot.fromJson(Json data) {
    if (data['supported'] is! bool || data['state'] is! String) throw const FormatException('Invalid voice state');
    supported=data['supported'] as bool;state=data['state'] as String;
    captureId=data['capture_id'] as String?;
    host=data['recorder_host'] as String? ?? 'coordinator computer';
    message=data['message'] as String? ?? '';
    mode=data['mode'] as String? ?? 'push_to_talk';
  }
  late final bool supported;
  late final String state,host,message,mode;
  late final String? captureId;
}
