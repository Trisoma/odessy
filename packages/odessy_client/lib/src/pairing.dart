import 'models.dart';
import 'setup.dart';

const pairingStates = {
  'closed','discoverable','authenticating','awaiting_approval','enrolling',
  'paired_no_access','authorizing_access','connecting','ready','rejected',
  'expired','cancelled','failed','incomplete'
};

/// Enrollment state is not execution authorization or connection health.
class PairingSnapshot {
  PairingSnapshot.fromJson(Json value) {
    if(value['schema_version'] != 1 || value['supported'] is! bool ||
        value['state'] is! String || !pairingStates.contains(value['state']) ||
        (value['supported'] == false && value['state'] != 'closed')) {
      throw const FormatException('Invalid pairing state');
    }
    supported=value['supported'] as bool;
    state=value['state'] as String;
    reason=value['reason'] is String ? value['reason'] as String : '';
    sessionId=value['session_id'] is String ? value['session_id'] as String : '';
    reviewToken=value['review_token'] is String ? value['review_token'] as String : '';
    if(reason.length>200 || sessionId.length>128 || reviewToken.length>256 ||
        (supported && state!='closed' && sessionId.isEmpty)) {
      throw const FormatException('Invalid pairing ownership');
    }
  }
  late final bool supported;
  late final String state,reason,sessionId,reviewToken;
}

abstract interface class PairingBackend {
  bool get isDemo;
  Future<PairingSnapshot> status();
  Future<PairingSnapshot> begin(String workspace);
  Future<PairingSnapshot> join(String address,String code);
  Future<PairingSnapshot> confirm(String sessionId,String reviewToken);
  Future<void> cancel();
  Future<void> close();
}

/// Fixed-operation adapter; the native boundary owns protocol availability and
/// session IDs. No process arguments, trust storage or cryptography live here.
class SetupPairingBackend implements PairingBackend {
  SetupPairingBackend(this.source,{this.ownsSource=false});
  final SetupBackend source;
  final bool ownsSource;
  bool _closed=false;
  @override bool get isDemo=>false;
  Future<PairingSnapshot> _call(String command,[Json values=const {}]) async {
    if(_closed) throw StateError('Pairing adapter closed');
    final request=SetupRequest(id:'pair-${DateTime.now().microsecondsSinceEpoch}',
      operation:'pair',selection:const {},parameters:{'action':'short-code','command':command,...values});
    final reply=await source.execute(request);
    if(!reply.ok) throw StateError('Pairing component unavailable; inspect and retry.');
    return PairingSnapshot.fromJson(reply.result);
  }
  @override Future<PairingSnapshot> status()=>_call('status');
  @override Future<PairingSnapshot> begin(String workspace)=>_call('begin',{'workspace':workspace});
  @override Future<PairingSnapshot> join(String address,String code) async {
    if(!RegExp(r'^[0-9]{6}$').hasMatch(code) || address.isEmpty || address.length>512 || address.contains(RegExp(r'\s'))) {
      throw const FormatException('Choose a device and enter six digits.');
    }
    return _call('join',{'address':address,'code':code});
  }
  @override Future<PairingSnapshot> confirm(String sessionId,String reviewToken) async {
    if(sessionId.isEmpty || sessionId.length>128 || reviewToken.isEmpty || reviewToken.length>256) {
      throw const FormatException('Pairing review is stale or unavailable.');
    }
    return _call('confirm',{'session_id':sessionId,'review_token':reviewToken});
  }
  @override Future<void> cancel() async {await source.cancel();}
  @override Future<void> close() async {
    if(_closed) return;
    _closed=true;
    if(ownsSource) await source.close();
  }
}
