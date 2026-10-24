import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';
class PackageSetupController extends ChangeNotifier {
  PackageSetupController(this.backend);
  final SetupBackend backend;
  Json context = {}, review = {}, result = {}, selection = {'role':'combined','voice':{'enabled':false}};
  String root = '', error = '', status = '';
  bool busy = false, interrupted = false, installed = false;
  int revision = 0;
  void edit({String? destination, Json? value}) {
    if (busy) return;
    if (destination != null) root = destination;
    if (value != null) selection = value;
    review = {}; revision++; notifyListeners();
  }
  Future<Json> call(String action, {String? confirm}) async {
    final reply = await backend.execute(SetupRequest(id:'package-${DateTime.now().microsecondsSinceEpoch}',operation:'manage',selection:const {},parameters:{'action':action,'root':root,'payload':context['payload_root'],'selection':selection,if(confirm != null)'confirm':confirm}));
    if (!reply.ok) throw StateError(reply.error);
    return reply.result;
  }
  Future<void> run(String action) async {
    if(busy) return;
    if(action == 'package-install' && (review['plan_id'] is! String || interrupted)) return;
    busy=true; error=''; status=action == 'package-install' ? 'Installing verified components. No model inference.' : 'Inspecting installation'; notifyListeners();
    try {
      final value=await call(action,confirm:action=='package-install'?review['plan_id'] as String:null);
      if(action=='context') {context=value; root=value['root'] as String? ?? '';}
      if(action=='package-review') {review=value; interrupted=false;}
      if(action=='package-install') {result=value; installed=true; review={};}
      status= action=='package-install' ? 'Installation completed' : 'Review completed';
    } catch(e) {error=e.toString(); if(action=='package-install') {interrupted=true; review={};} status='No automatic retry. Inspect before recovery.';}
    finally {busy=false;notifyListeners();}
  }
  Future<void> cancel() async {if(!busy)return; interrupted=true;review={}; await backend.cancel();}
  Future<void> close() async {await cancel();await backend.close();}
}
