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
    // Additive, non-authoritative detail. Each field is only accepted in the shape
    // the native component documents, so malformed data cannot look like progress.
    role=value['role'] is String && (value['role'] as String).length<=16 ? value['role'] as String : '';
    message=value['message'] is String ? (value['message'] as String).substring(0, ((value['message'] as String).length>300?300:(value['message'] as String).length)) : '';
    error=value['error'] is String ? (value['error'] as String).substring(0, ((value['error'] as String).length>200?200:(value['error'] as String).length)) : '';
    final seconds=value['seconds_remaining'];
    secondsRemaining=seconds is int && seconds>=0 && seconds<=86400 ? seconds : 0;
    final attempts=value['attempts_remaining'];
    attemptsRemaining=attempts is int && attempts>=0 && attempts<=3 ? attempts : null;
    cancelled=value['cancelled']==true;
    final peer=value['peer'];
    // `late final` fields may be assigned once, so the peer detail is resolved into
    // locals first and written once.
    final peerAliasValue=peer is Map && peer['alias'] is String ? peer['alias'] as String : '';
    final peerInstallationValue=peer is Map && peer['installation_id'] is String ? peer['installation_id'] as String : '';
    final peerFingerprintValue=peer is Map && peer['fingerprint_sha256'] is String ? peer['fingerprint_sha256'] as String : '';
    peerAlias=peerAliasValue;
    peerInstallationId=peerInstallationValue;
    peerFingerprint=peerFingerprintValue;
  }
  late final bool supported;
  late final String state,reason,sessionId,reviewToken;
  late final String role,message,error,peerAlias,peerInstallationId,peerFingerprint;
  late final int secondsRemaining;
  late final int? attemptsRemaining;
  late final bool cancelled;

  /// The other computer's operator must approve the request there.
  bool get awaitingApproval => state=='awaiting_approval';
  /// A finished attempt at this window; the user can ask for a new code.
  bool get canStartOver =>
      const {'closed','expired','rejected','cancelled','failed'}.contains(state);
  /// The outcome is unknown; the user must inspect rather than retry blindly.
  bool get needsInspection => state=='incomplete';
  /// Identity is enrolled. Execution still needs an explicit consent review.
  bool get paired => state=='paired_no_access'||state=='ready';
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
  SetupPairingBackend(this.source,{this.ownsSource=false,this.link=''});
  final SetupBackend source;
  final bool ownsSource;
  final String link;
  bool _closed=false;
  String? _ownedSession;
  @override bool get isDemo=>false;
  Future<PairingSnapshot> _call(String command,[Json values=const {}]) async {
    if(_closed) throw StateError('Pairing adapter closed');
    final request=SetupRequest(id:'pair-${DateTime.now().microsecondsSinceEpoch}',
      operation:'pair',selection:const {},parameters:{'action':'short-code','command':command,if(link.isNotEmpty)'link':link,...values});
    final reply=await source.execute(request);
    if(!reply.ok) throw StateError('Pairing component unavailable; inspect and retry.');
    final snapshot=PairingSnapshot.fromJson(reply.result);
    if ((command=='join'||command=='begin') && snapshot.sessionId.isNotEmpty) {
      _ownedSession=snapshot.sessionId;
    }
    return snapshot;
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
  @override Future<void> cancel() async {
    await source.cancel();
    final session=_ownedSession;
    if(session!=null) {
      await _call('cancel',{'session_id':session});
    }
  }
  @override Future<void> close() async {
    if(_closed) return;
    _closed=true;
    if(ownsSource) await source.close();
  }
}
